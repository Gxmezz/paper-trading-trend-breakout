"""Reversión a la media en rango: extremo de Bollinger con filtro de NO tendencia (ADX bajo + EMA50 H4 plana).
Entrada contra el extremo (al tocar fuera de banda o al volver dentro), salida al cruzar la media (XL/XS) u objetivo tp_r,
stop fijo en ATR y max_bars."""
import itertools, numpy as np
from ind import adx, bollinger, ema, htf, atr

NAME = "MR: Bollinger extremo + ADX bajo + H4 plano"
TFS = ("M30", "H1")

def grid():
    G = []
    for k, amax, mode, sa, ex, flat in itertools.product([2.0, 2.5], [20, 25, 30], ["touch", "reentry"],
                                                        [1.5, 2.5], ["mid", 1.0, 1.5], [None, 0.5]):
        p = dict(n=20, k=k, adx_max=amax, mode=mode, stop_atr=sa, flat=flat, max_bars=24)
        if ex == "mid": p["exit"] = "mid"
        else: p["exit"] = "tp"; p["tp_r"] = ex
        G.append(p)
    return G

def _slope_h4(x):
    e = ema(x.Close, 50)
    return (e - e.shift(5)).abs() / atr(x, 14)          # pendiente de 5 velas H4 en ATRs H4

def signals(df, p):
    c = df.Close
    lo, mid, up = bollinger(c, p["n"], p["k"])
    ok = (adx(df, 14) < p["adx_max"]).to_numpy(copy=True)
    if p["flat"] is not None:
        ok = ok & (htf(df, "4h", _slope_h4) < p["flat"]).fillna(False).to_numpy()
    if p["mode"] == "touch":
        L = (c < lo); S = (c > up)
    else:                                              # cerró fuera y ahora vuelve dentro
        L = (c.shift() < lo.shift()) & (c > lo); S = (c.shift() > up.shift()) & (c < up)
    L = L.to_numpy() & ok; S = S.to_numpy() & ok
    out = dict(L=L, S=S)
    if p["exit"] == "mid":
        out["XL"] = (c >= mid).values; out["XS"] = (c <= mid).values
    return out
