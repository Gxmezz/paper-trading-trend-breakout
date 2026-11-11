"""Intermercado 1: el índice dólar (USDX) como filtro de dirección. Solo largos de oro con ruptura Donchian cuando el
dólar cae con fuerza (rentabilidad de USDX en n velas < -thr desviaciones típicas móviles de esa rentabilidad); cortos al
revés. Salida: trailing ATR o cuando el dólar deja de acompañar (momento de USDX cambia de signo)."""
import itertools, numpy as np
from ind import other

NAME = "USDX momento como filtro + ruptura Donchian oro"
TFS = ("H1", "M30")

def grid():
    return [dict(n=n, thr=t, don=d, stop_atr=s, trail=True, shorts=sh)
            for n, t, d, s, sh in itertools.product([24, 48, 96], [0.0, 0.5, 1.0], [20, 40], [2.0, 3.0, 4.0], [False, True])]

def signals(df, p):
    c = df.Close; n = p["n"]
    u = np.log(other(df, "USDX"))
    r = u - u.shift(n)
    z = r / r.rolling(1000, min_periods=300).std()
    up = df.High.rolling(p["don"]).max().shift(1); dn = df.Low.rolling(p["don"]).min().shift(1)
    L = (c > up) & (z < -p["thr"])
    S = ((c < dn) & (z > p["thr"])) if p["shorts"] else (c < -1)
    return dict(L=L.fillna(False).to_numpy(), S=S.fillna(False).to_numpy(),
                XL=(z > 0).fillna(False).to_numpy(), XS=(z < 0).fillna(False).to_numpy())
