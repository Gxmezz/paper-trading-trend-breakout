"""Filtro de régimen por expansión de volatilidad: ATR H4 en percentil alto de sus últimas 120 velas H4 (~20 días, ventana
móvil, sin datos futuros) + EMAs H4 20/50 alineadas + precio a >= k ATR H4 de la EMA50 H4. Entrada: ruptura Donchian en
la TF a favor. Salida: trailing ATR o cruce de EMAs H4."""
import itertools, numpy as np
from ind import ema, atr, htf

NAME = "Régimen volatilidad H4 (percentil) + EMAs H4 + distancia ATR + ruptura Donchian"
TFS = ("H1", "M30")

def grid():
    return [dict(vp=v, dist=k, don=d, stop_atr=s, trail=True, shorts=sh)
            for v, k, d, s, sh in itertools.product([0.5, 0.7], [0.0, 1.0, 2.0], [10, 20, 40], [2.0, 3.0, 4.0], [False, True])]

def _pct(s, n=120):
    return s.rolling(n).apply(lambda w: (w[:-1] < w[-1]).mean(), raw=True)

def signals(df, p):
    c = df.Close
    vp = htf(df, "4h", lambda x: _pct(atr(x, 14)))
    f = htf(df, "4h", lambda x: ema(x.Close, 20)); s = htf(df, "4h", lambda x: ema(x.Close, 50))
    a4 = htf(df, "4h", lambda x: atr(x, 14))
    z = (c - s) / a4
    reg = vp > p["vp"]
    bull = reg & (f > s) & (z > p["dist"]); bear = reg & (f < s) & (z < -p["dist"])
    up = df.High.rolling(p["don"]).max().shift(1); dn = df.Low.rolling(p["don"]).min().shift(1)
    L = bull & (c > up)
    S = (bear & (c < dn)) if p["shorts"] else (c < -1)
    return dict(L=L.values, S=S.values, XL=(f < s).values, XS=(f > s).values)
