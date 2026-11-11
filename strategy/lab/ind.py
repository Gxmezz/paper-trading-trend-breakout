"""Indicadores sin mirar al futuro. Todo lo de temporalidad superior va desplazado una vela de esa temporalidad."""
import numpy as np, pandas as pd
from engine import atr  # noqa: F401  (re-export)

NY = "America/New_York"

def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()

def rsi(c, n):
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))

def adx(df, n=14):
    up = df.High.diff(); dn = -df.Low.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), df.index)
    tr = atr(df, n)
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / tr
    ndi = 100 * ndm.ewm(alpha=1 / n, adjust=False).mean() / tr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()

def bollinger(c, n=20, k=2.0):
    m = c.rolling(n).mean(); s = c.rolling(n).std()
    return m - k * s, m, m + k * s

def htf(df, rule, fn):
    """Calcula fn(velas de temporalidad `rule`) y lo lleva a df SIN mirar al futuro (valor de la vela superior ya cerrada).
    Ej.: htf(df, "4h", lambda x: ema(x.Close, 50))"""
    x = df.resample(rule, label="left", closed="left").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    v = fn(x)
    v.index = v.index + pd.tseries.frequencies.to_offset(rule)   # disponible al cierre de la vela superior
    return v.reindex(df.index.union(v.index)).ffill().reindex(df.index)

def ny_time(df):
    """Hora y minuto de Nueva York de la APERTURA de cada vela (para sesiones y noticias)."""
    t = df.index.tz_convert(NY)
    return t.hour.values, t.minute.values, t.weekday.values

def bar_close_ny(df, minutes):
    """Hora (decimal) de NY al CIERRE de cada vela de `minutes` minutos."""
    t = (df.index + pd.Timedelta(minutes=minutes)).tz_convert(NY)
    return t.hour.values + t.minute.values / 60

# ---------- otros mercados (MT5 Vantage, M30 en UTC) ----------
_OTHER = {}
OTHER = ("XAGUSD", "EURUSD", "USDJPY", "NAS100", "SP500", "USDX")
def other(df, sym, col="Close"):
    """Precio de otro mercado alineado con las velas de df: valor al CIERRE de cada vela de df (misma hora que la señal,
    sin mirar al futuro). Huecos (horarios distintos) -> último valor conocido. sym en OTHER."""
    if sym not in _OTHER:
        from engine import D
        _OTHER[sym] = pd.read_pickle(D / f"{sym}_mt5_M30.pkl")
    x = _OTHER[sym]
    step = df.index.to_series().diff().mode().iloc[0]      # duración de la vela de df (30 min o 1 h)
    if col == "Close":   # cierre de la vela M30 que termina a la vez que la vela de df
        s = x.Close.copy(); s.index = s.index + pd.Timedelta(minutes=30)
        return s.reindex(df.index.union(s.index)).ffill().reindex(df.index + step).set_axis(df.index)
    raise ValueError("solo col='Close'")
