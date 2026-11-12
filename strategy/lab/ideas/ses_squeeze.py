"""Compresión -> expansión: si el ancho de la caja de las últimas N velas (normalizado por el precio) está en un percentil
bajo de su propio pasado (ventana móvil de 1000 velas), la primera vela que cierra fuera de esa caja abre en esa dirección.
Opcionalmente solo en horas activas (02-12 NY, Londres + NY). Stop en el lado opuesto de la caja o 1,5 ATR; salida por tiempo."""
import itertools, numpy as np, pandas as pd
from ind import atr, bar_close_ny

NAME = "Sesión/volatilidad: compresión de caja N velas y ruptura"
TFS = ("M30", "H1")

def grid():
    return [dict(n=n, q=q, active=ac, stop=st, max_bars=mb, tp_r=tp)
            for n, q, ac, st, mb, tp in itertools.product([6, 12], [0.1, 0.2, 0.3], [False, True], ["box", "atr"], [8, 16, 32], [None, 2.0])]

def signals(df, p):
    n = p["n"]; c = df.Close; a = atr(df)
    bh = df.High.rolling(n).max().shift(1); bl = df.Low.rolling(n).min().shift(1)   # caja de las N velas ANTERIORES
    wn = (bh - bl) / c.shift(1)
    rk = wn.rolling(1000, min_periods=300).rank(pct=True)                          # percentil solo con pasado
    sq = rk <= p["q"]
    ok = sq.values.copy()
    if p["active"]:
        hc = bar_close_ny(df, 60 if (len(df) > 1 and df.index[1] - df.index[0] >= pd.Timedelta("1h")) else 30)
        ok &= (hc > 2) & (hc <= 12)
    L = ok & (c > bh).values; S = ok & (c < bl).values
    if p["stop"] == "box":
        dist = np.where(L, c - bl, bh - c)
        dist = np.maximum(np.nan_to_num(dist, nan=0.0), 0.5 * a.values)
    else:
        dist = 1.5 * a.values
    return dict(L=L, S=S, stop=dist)
