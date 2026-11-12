"""
Datos e indicadores del laboratorio DIARIO (oro 2000-2026).

Velas diarias: XAU_D1.pkl = futuro COMEX (Yahoo GC=F) hasta 2018-03-16 + spot de Vantage (MT5) desde 2018-03-19.
Cada vela se indexa en la APERTURA de la sesión (22:00 UTC del día anterior) para que el motor común cuente bien días y swap.
Sin mirar al futuro: señal al cierre de la vela i -> entrada al open de i+1 (lo hace el motor).
"""
import os, sys, numpy as np, pandas as pd
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from engine import D, atr                     # noqa: F401
from ind import ema, sma, rsi, adx, bollinger  # noqa: F401  (funcionan igual en diario)

# Activo del laboratorio: variable de entorno LAB_ASSET (la fija djudge.py --asset). XAU por defecto.
#   cost/stress: (coste fijo por lado en precio, coste por lado en fracción del precio) -> se usa el mayor.
#   swap (índices): largos pagan tipo 3 meses + 3 % - dividendos; cortos cobran tipo - 3 % - dividendos (si sale negativo,
#   pagan). Así es el CFD de contado de Vantage (hoy NAS100 largo ≈ -7,4 %/año, corto ≈ +1,5 %/año). Noches reales entre velas.
#   div: rentabilidad por dividendo media aproximada (el índice de precio no la incluye; el CFD la abona a los largos).
ASSETS = {
    "XAU": dict(file="XAU_D1.pkl", cost=(0.30, 0.0001), stress=(0.50, 0.0002), train0=2002, test0=2005, swap=None, div=0.0,
                name="Oro"),
    "NDX": dict(file="NDX_D1.pkl", cost=(0.0, 0.0001), stress=(0.0, 0.0002), train0=1987, test0=1990, swap=3.0, div=0.8,
                name="Nasdaq 100"),
    "SPX": dict(file="SPX_D1.pkl", cost=(0.0, 0.0001), stress=(0.0, 0.0002), train0=1987, test0=1990, swap=3.0, div=2.0,
                name="S&P 500"),
}
ASSET = os.environ.get("LAB_ASSET", "XAU").upper()
CFG = ASSETS[ASSET]

_RAW = None
def raw():
    global _RAW
    if _RAW is None:
        x = pd.read_pickle(D / CFG["file"])
        idx = (x.index - pd.Timedelta(days=1)).tz_localize("UTC") + pd.Timedelta(hours=22)
        _RAW = pd.DataFrame(x[["Open", "High", "Low", "Close"]].values, index=idx, columns=["Open", "High", "Low", "Close"])
        _RAW["date"] = x.index.values            # fecha de la sesión (día de cierre)
        if CFG["swap"] is not None:              # swap diario por época (fracción anual) y noches desde la vela anterior
            rt = pd.read_pickle(D / "RATE_D1.pkl").sort_index()
            r = rt.reindex(pd.DatetimeIndex(x.index).union(rt.index)).ffill().reindex(x.index).values / 100
            _RAW["swl"] = r + (CFG["swap"] - CFG["div"]) / 100
            _RAW["sws"] = r - (CFG["swap"] + CFG["div"]) / 100
            d = pd.Series(pd.DatetimeIndex(x.index)).diff().dt.days.fillna(1).clip(1, 5).values
            _RAW["swn"] = d
    return _RAW

def load(start, end, warmup_days=420):
    """(df, i0) con velas diarias desde start-warmup hasta end; i0 = primera vela del periodo."""
    r = raw(); a = pd.Timestamp(start); b = pd.Timestamp(end) if end else None
    m = (r.date >= a - pd.Timedelta(days=warmup_days)) & ((r.date <= b) if b is not None else True)
    df = r[m]
    return df, int(np.searchsorted(df.date.values, np.datetime64(a)))

def _period(df, freq):
    return pd.DatetimeIndex(df.date).to_period(freq)

def prev_period(df, freq, how):
    """Valor del periodo ANTERIOR ya cerrado (freq 'W' semana, 'M' mes) para cada vela.
    how: 'High' (máximo), 'Low' (mínimo), 'Close' (cierre), 'Open' (apertura)."""
    per = _period(df, freq)
    agg = {"High": "max", "Low": "min", "Close": "last", "Open": "first"}[how]
    g = df[how].groupby(per).agg(agg)
    prev = g.shift(1)
    return pd.Series(prev.reindex(per).values, index=df.index)

def period_closes(df, freq):
    """Cierre de cada periodo (semana 'W' / mes 'M'). OJO: el último periodo puede estar incompleto;
    úsalo siempre a través de align_prev(), que da a cada vela el valor del periodo ANTERIOR (ya cerrado).
    Ej.: sma10w = align_prev(df, "W", sma(period_closes(df, "W"), 10))"""
    per = _period(df, freq)
    return df.Close.groupby(per).last()

def align_prev(df, freq, s_per):
    """Lleva una serie indexada por periodo (ya calculada sobre periodos CERRADOS) a cada vela diaria usando el
    valor del periodo anterior al de la vela (sin mirar al futuro)."""
    per = _period(df, freq)
    return pd.Series(s_per.shift(1).reindex(per).values, index=df.index)

def weekday(df): return pd.DatetimeIndex(df.date).weekday.values      # 0 = lunes
def month(df): return pd.DatetimeIndex(df.date).month.values
def year(df): return pd.DatetimeIndex(df.date).year.values

_OTHER = {}
def other(df, sym):
    """Cierre diario de otro mercado (XAGUSD, EURUSD, USDJPY, NAS100, SP500, USDX) desde las velas M30 de MT5,
    alineado al cierre de cada vela de oro (17:00 NY). SOLO hay datos desde 2020: antes devuelve NaN."""
    if sym not in _OTHER:
        x = pd.read_pickle(D / f"{sym}_mt5_M30.pkl").Close
        x.index = x.index + pd.Timedelta(minutes=30)          # hora de cierre de cada vela M30
        _OTHER[sym] = x
    x = _OTHER[sym]; close_t = df.index + pd.Timedelta(days=1)  # cierre de la sesión diaria (22:00 UTC aprox.)
    return pd.Series(x.reindex(x.index.union(close_t)).ffill().reindex(close_t).values, index=df.index)

_VIX = None
def vix(df):
    """Cierre del VIX de cada sesión (Yahoo ^VIX, desde 1990; antes NaN). Se conoce al cierre: úsalo para señales al cierre."""
    global _VIX
    if _VIX is None: _VIX = pd.read_pickle(D / "VIX_D1.pkl").Close
    return pd.Series(_VIX.reindex(pd.DatetimeIndex(df.date)).values, index=df.index)
