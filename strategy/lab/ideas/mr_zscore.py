"""Sobre-extensión fuerte (z-score del cierre frente a su media de n velas, |z| >= zk) en régimen lateral DIARIO
(ADX diario bajo, de la vela diaria ya cerrada). Pocas operaciones con recorrido grande hasta la media para que los costes
pesen poco. Opcional: vela de confirmación a favor de la vuelta. Salida al cruzar la media (XL/XS) u objetivo tp_r."""
import itertools, numpy as np
from ind import adx, htf

NAME = "MR: z-score extremo frente a la media + ADX diario bajo"
TFS = ("M30", "H1")

def grid():
    G = []
    for n, zk, amax, ex, sa, conf in itertools.product([48, 96], [2.5, 3.0], [20, 25, None], ["mid", 1.5],
                                                      [2.0, 3.0], [False, True]):
        p = dict(n=n, zk=zk, adx_d=amax, exit=ex, stop_atr=sa, confirm=conf, max_bars=n // 2)
        if ex != "mid": p["tp_r"] = ex
        G.append(p)
    return G

def signals(df, p):
    o, c = df.Open, df.Close
    m = c.rolling(p["n"]).mean(); sd = c.rolling(p["n"]).std()
    z = (c - m) / sd
    L = (z <= -p["zk"]); S = (z >= p["zk"])
    if p["confirm"]:
        L &= c > o; S &= c < o
    L = L.fillna(False).to_numpy(); S = S.fillna(False).to_numpy()
    if p["adx_d"] is not None:
        ok = (htf(df, "1D", lambda x: adx(x, 14)) < p["adx_d"]).fillna(False).to_numpy()
        L = L & ok; S = S & ok
    out = dict(L=L, S=S)
    if p["exit"] == "mid":
        out["XL"] = (z >= 0).fillna(False).to_numpy(); out["XS"] = (z <= 0).fillna(False).to_numpy()
    return out
