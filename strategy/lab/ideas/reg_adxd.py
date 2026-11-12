"""Filtro de régimen diario: ADX(14) diario > umbral Y precio a más de k ATR diarios de la EMA50 diaria (tendencia fuerte).
Entrada: reanudación en la TF (cierre cruza de nuevo la EMA20 de la TF a favor del régimen). Salida: trailing ATR,
objetivo opcional en R, o cierre cuando el diario vuelve al otro lado de la EMA50."""
import itertools, numpy as np
from ind import ema, adx, atr, htf

NAME = "Régimen ADX diario + distancia EMA50 D en ATR + reanudación EMA20"
TFS = ("H1", "M30")

def grid():
    return [dict(adx_thr=a, dist=k, stop_atr=s, tp_r=t, trail=t is None, shorts=sh)
            for a, k, s, t, sh in itertools.product([20, 25, 30], [0.5, 1.0, 1.5], [2.0, 3.0, 4.0], [None, 2.0, 3.0], [False, True])]

def signals(df, p):
    c = df.Close
    ad = htf(df, "1D", lambda x: adx(x, 14))
    e = htf(df, "1D", lambda x: ema(x.Close, 50)); ad_atr = htf(df, "1D", lambda x: atr(x, 14))
    dc = htf(df, "1D", lambda x: x.Close)
    z = (dc - e) / ad_atr
    strong = ad > p["adx_thr"]
    bull = strong & (z > p["dist"]); bear = strong & (z < -p["dist"])
    m = ema(c, 20)
    xu = (c > m) & (c.shift() <= m.shift()); xd = (c < m) & (c.shift() >= m.shift())
    L = bull & xu
    S = (bear & xd) if p["shorts"] else (c < -1)
    return dict(L=L.values, S=S.values, XL=(dc < e).values, XS=(dc > e).values)
