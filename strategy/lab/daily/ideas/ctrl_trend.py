"""Control diario: tendencia clásica (ruptura Donchian de N días + filtro de media + stop ATR con trailing)."""
import itertools
from dind import sma

NAME = "Control: ruptura Donchian diaria + media"

def grid():
    return [dict(don=d, ma=m, stop_atr=k, trail=True, shorts=sh)
            for d, m, k, sh in itertools.product([20, 55, 100], [50, 200], [2.0, 3.0, 4.0, 6.0], [False, True])]

def signals(df, p):
    c = df.Close; t = sma(c, p["ma"])
    up = df.High.rolling(p["don"]).max().shift(1); dn = df.Low.rolling(p["don"]).min().shift(1)
    L = (c > up) & (c > t); S = ((c < dn) & (c < t)) if p["shorts"] else (c < -1)
    return dict(L=L.to_numpy(copy=True), S=S.to_numpy(copy=True))
