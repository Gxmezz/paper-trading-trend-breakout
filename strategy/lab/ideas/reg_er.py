"""Filtro de régimen por eficiencia de Kaufman en H4 + alineación de EMAs H4; entrada por ruptura Donchian en la TF.
Solo se opera si el mercado H4 es 'eficiente' (ER alto = tendencia limpia) y las EMAs H4 apuntan en la misma dirección.
Salida: trailing ATR o cuando el régimen deja de ser alcista/bajista (EMAs H4 se cruzan)."""
import itertools, numpy as np
from ind import ema, htf

NAME = "Régimen ER Kaufman H4 + EMA H4 + ruptura Donchian"
TFS = ("H1", "M30")

def grid():
    return [dict(er_n=n, er_thr=t, don=d, stop_atr=k, trail=True, shorts=sh)
            for n, t, d, k, sh in itertools.product([20, 40], [0.20, 0.30, 0.40], [10, 20, 40], [2.0, 3.0, 4.0], [False, True])]

def _er(c, n):
    return (c - c.shift(n)).abs() / c.diff().abs().rolling(n).sum()

def signals(df, p):
    c = df.Close; n = p["er_n"]
    er = htf(df, "4h", lambda x: _er(x.Close, n))
    f = htf(df, "4h", lambda x: ema(x.Close, 20)); s = htf(df, "4h", lambda x: ema(x.Close, 50))
    trend = er > p["er_thr"]
    bull = trend & (f > s) & (c > f); bear = trend & (f < s) & (c < f)
    up = df.High.rolling(p["don"]).max().shift(1); dn = df.Low.rolling(p["don"]).min().shift(1)
    L = bull & (c > up)
    S = (bear & (c < dn)) if p["shorts"] else (c < -1)
    return dict(L=L.values, S=S.values, XL=(f < s).values, XS=(f > s).values)
