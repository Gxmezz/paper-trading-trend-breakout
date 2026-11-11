"""Control: familia de la estrategia actual (EMA + ruptura Donchian + stop ATR con trailing). Sirve para validar el juez."""
import itertools
from ind import ema

NAME = "Control: ruptura Donchian + EMA (actual)"
TFS = ("H1", "M30")

def grid():
    return [dict(ema=e, don=d, stop_atr=k, trail=True, shorts=sh)
            for e, d, k, sh in itertools.product([50, 100, 200], [10, 20, 40, 60], [2.0, 3.0, 4.0, 5.0], [False, True])]

def signals(df, p):
    c = df.Close; t = ema(c, p["ema"])
    up = df.High.rolling(p["don"]).max().shift(1); dn = df.Low.rolling(p["don"]).min().shift(1)
    L = (c > up) & (c > t); S = ((c < dn) & (c < t)) if p["shorts"] else (c < -1)
    return dict(L=L.values, S=S.values)
