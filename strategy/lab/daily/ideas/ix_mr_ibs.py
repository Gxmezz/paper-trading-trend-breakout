"""Índices, reversión corta en largos por IBS: cierre en la parte baja del rango del día (IBS = (C-L)/(H-L) < th),
opcionalmente con cierre sobre la media larga -> largo. Salida: cierre > máximo anterior, IBS > 0,7 o cierre > SMA5,
o tras max_bars días; stop amplio en ATR."""
import itertools, numpy as np
from dind import sma

NAME = "Índices: IBS bajo (solo largos)"

def grid():
    return [dict(ibs=b, ma=m, exit=x, stop_atr=k, max_bars=mb)
            for b, m, x, k, mb in itertools.product([0.1, 0.2, 0.3], [0, 100, 200], ["prevhigh", "ibs", "sma5"],
                                                    [3.0, 6.0, 10.0], [3, 6])]

def signals(df, p):
    c, h, l = df.Close, df.High, df.Low
    rng = (h - l).replace(0, np.nan); ibs = ((c - l) / rng).fillna(0.5)
    L = ibs < p["ibs"]
    if p["ma"]: L = L & (c > sma(c, p["ma"]))
    if p["exit"] == "prevhigh": XL = c > h.shift(1)
    elif p["exit"] == "ibs": XL = ibs > 0.7
    else: XL = c > sma(c, 5)
    S = c < -1
    return dict(L=L.to_numpy(copy=True), S=S.to_numpy(copy=True), XL=XL.to_numpy(copy=True))
