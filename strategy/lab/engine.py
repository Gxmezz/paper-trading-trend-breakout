"""
Motor común del laboratorio: cuenta de 1000 € en Vantage (XAUUSD, 1 oz por 0,01 lote), costes, swap y gestor de riesgo.

Una idea entrega señales; el motor decide tamaño, stops, objetivo y reglas de riesgo. Todas las ideas se miden igual.

Contrato de una idea (strategy/lab/ideas/<nombre>.py):
    NAME = "texto corto"
    TFS = ("M30", "H1")                  # temporalidades donde se prueba (solo M30 y/o H1: es lo que hay en datos reales)
    def grid() -> list[dict]             # combinaciones de parámetros (máximo 300 en total)
    def signals(df, p) -> dict           # df: velas OHLC en UTC de esa temporalidad. Devuelve arrays (len(df)):
        L, S   : bool. Señal al CIERRE de la vela i -> entrada al OPEN de la vela i+1. Prohibido mirar al futuro.
        XL, XS : bool opcional. Salida de largos/cortos al open siguiente.
        stop   : float opcional. Distancia del stop en $ (si no, stop_atr × ATR14 de la vela de la señal).
    Parámetros de salida que el motor entiende dentro de p:
        stop_atr (2.0), tp_r (None = sin objetivo; objetivo = tp_r × distancia del stop),
        trail (False; trailing por ATR al cierre, mult = trail_atr o stop_atr), max_bars (None).
"""
from dataclasses import dataclass
import numpy as np, pandas as pd
from pathlib import Path

D = Path(__file__).resolve().parent.parent / "data"
EURUSD = 1.12
CAP = 1000 * EURUSD
STEP = 1.0
MAX_LEV = 50.0
SWAP_LONG = 0.06
DATA_FILE = "XAUUSD_mt5_M30.pkl"
RULE = {"M30": "30min", "H1": "1h"}

@dataclass
class RiskConfig:
    risk: float = 0.01               # riesgo por operación (fracción del capital)
    max_risk_min_lot: float = 0.03   # si 1 oz arriesga más que esto, no se opera
    daily_loss: float | None = None  # p. ej. 0.05: el día se para al -5 % (y no se abre si el riesgo no cabe)
    max_consec: int | None = None    # p. ej. 4 pérdidas seguidas paran el día
    min_rr: float | None = None      # p. ej. 2.0: solo operaciones con objetivo >= 2R (si la idea tiene tp_r)
    dd_brake: float | None = None    # p. ej. 0.10: con caída > 10 % desde el máximo, riesgo a la mitad
    max_trades_day: int | None = None

# ---------- datos ----------
_RAW = None
def load(start, end, tf, warmup_days=120):
    """Velas de XAUUSD real (MT5 Vantage) en UTC. Devuelve (df, i0): i0 = primera vela del periodo."""
    global _RAW
    if _RAW is None:
        df = pd.read_pickle(D / DATA_FILE)[["Open", "High", "Low", "Close"]]
        wd, h = df.index.weekday, df.index.hour
        _RAW = df[~((wd == 5) | ((wd == 6) & (h < 22)))]
    a = pd.Timestamp(start, tz="UTC"); b = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1) if end else None
    df = _RAW[a - pd.Timedelta(days=warmup_days):b]
    if tf != "M30":
        df = df.resample(RULE[tf]).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    return df, int(df.index.searchsorted(a))

def atr(df, n=14):
    pc = df.Close.shift()
    tr = pd.concat([df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()

# ---------- simulación ----------
def simulate(df, i0, sig, p, cost=0.20, rc=None, trades=None, curve=None):
    rc = rc or RiskConfig()
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    a = atr(df).values
    n = len(o)
    L = np.asarray(sig["L"], bool); S = np.asarray(sig["S"], bool)
    XL = sig.get("XL"); XS = sig.get("XS")
    XL = np.zeros(n, bool) if XL is None else np.asarray(XL, bool)
    XS = np.zeros(n, bool) if XS is None else np.asarray(XS, bool)
    sd = sig.get("stop")
    sd = p.get("stop_atr", 2.0) * a if sd is None else np.asarray(sd, float)
    tp_r = p.get("tp_r"); trail = p.get("trail", False); tk = p.get("trail_atr", p.get("stop_atr", 2.0)); mb = p.get("max_bars")
    day = (df.index - pd.Timedelta(hours=22)).normalize().asi8
    wdy = (df.index - pd.Timedelta(hours=22)).weekday.values
    eq = CAP; peak = CAP; mdd = 0.0; pos = 0; q = 0.0; entry = stop = tp = 0.0; rk = 0.0; t_in = 0
    pend = 0; pend_exit = False; gp = gl = 0.0; ntr = 0; tpnl = 0.0; skipped = 0
    d_start = CAP; consec = 0; locked = False; d_trades = 0; cur = CAP

    def close(px, i):
        nonlocal eq, pos, gp, gl, ntr, tpnl, consec, locked
        pnl = pos * q * (px - entry); eq += pnl; tpnl += pnl
        gp += max(tpnl, 0); gl += max(-tpnl, 0); ntr += 1
        if trades is not None:
            trades.append(dict(t_in=df.index[t_in], t_out=df.index[i], dir=pos, pnl=tpnl / EURUSD, R=tpnl / rk if rk else 0.0))
        consec = consec + 1 if tpnl < 0 else 0
        if (rc.max_consec and consec >= rc.max_consec) or (rc.daily_loss and eq <= d_start * (1 - rc.daily_loss)): locked = True
        tpnl = 0.0; pos = 0

    for i in range(i0, n):
        if day[i] != day[i - 1]:
            d_start = eq + (pos * q * (o[i] - entry) if pos else 0); consec = 0; locked = False; d_trades = 0
            if pos == 1:
                sw = q * o[i] * SWAP_LONG / 365 * (3 if wdy[i] == 2 else 1); eq -= sw; tpnl -= sw
        if pend_exit and pos: close(o[i] - pos * cost, i)
        pend_exit = False
        if pend and pos == 0:
            dist = sd[i - 1]; px = o[i]
            r = rc.risk * (0.5 if rc.dd_brake and eq < peak * (1 - rc.dd_brake) else 1.0)
            qq = np.floor(eq * r / dist / STEP) * STEP if dist > 0 and np.isfinite(dist) else 0
            if 0 < dist and np.isfinite(dist) and qq < STEP: qq = STEP if STEP * dist <= eq * rc.max_risk_min_lot else 0
            risk_amt = qq * (dist + 2 * cost)
            ok = qq > 0 and qq * px <= eq * MAX_LEV
            if ok and rc.daily_loss and (d_start - eq) + risk_amt > d_start * rc.daily_loss: ok = False
            if ok and rc.min_rr and (tp_r is not None and tp_r < rc.min_rr): ok = False
            if ok:
                pos = pend; q = qq; entry = px + pos * cost; stop = px - pos * dist; rk = risk_amt; t_in = i; d_trades += 1
                tp = px + pos * tp_r * dist if tp_r else np.nan
            else: skipped += 1
        pend = 0
        if pos:
            hit_s = l[i] <= stop if pos == 1 else h[i] >= stop
            hit_t = tp_r and (h[i] >= tp if pos == 1 else l[i] <= tp)
            if hit_s:      # si en la misma vela tocan stop y objetivo, se asume el stop (conservador)
                close((min(o[i], stop) if pos == 1 else max(o[i], stop)) - pos * cost, i)
            elif hit_t:
                close((max(o[i], tp) if pos == 1 else min(o[i], tp)) - pos * cost, i)
            else:
                if trail:
                    ns = c[i] - pos * tk * a[i]
                    stop = max(stop, ns) if pos == 1 else min(stop, ns)
                if (pos == 1 and XL[i]) or (pos == -1 and XS[i]) or (mb and i - t_in + 1 >= mb): pend_exit = True
        cur = eq + (pos * q * (c[i] - entry) if pos else 0)
        peak = max(peak, cur); mdd = min(mdd, cur / peak - 1)
        if curve is not None: curve.append(cur / EURUSD)
        if cur < CAP * 0.1: break
        if pos == 0 and not locked and i + 1 < n and not (rc.max_trades_day and d_trades >= rc.max_trades_day):
            if L[i]: pend = 1
            elif S[i]: pend = -1
    days = (df.index[-1] - df.index[i0]).days or 1
    ret = cur / CAP - 1
    return dict(trades=ntr, PF=round(gp / gl, 2) if gl else (99.0 if gp else 0.0), ret=round(ret * 100, 1),
                monthly=round(((1 + ret) ** (30.4 / days) - 1) * 100, 2) if ret > -1 else -100.0,
                maxDD=round(mdd * 100, 1), skipped=skipped, gp=round(gp, 2), gl=round(gl, 2))
