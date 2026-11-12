"""Intermercado 3: puntuación macro lenta para el oro, sin usar el precio del oro. Cuatro votos (+1/-1) con la rentabilidad
de las últimas n velas: dólar (USDX cae = +1), tipos reales vía USDJPY (cae = +1), plata (sube = +1) y refugio (NAS100
cae = +1). Entrada larga al cruzar la puntuación a >= m (cortos a <= -m), salida cuando la puntuación vuelve a <= 0
o por stop ATR amplio (con o sin trailing). Pocas operaciones de recorrido largo."""
import itertools, numpy as np
from ind import other

NAME = "Puntuación macro (USDX, USDJPY, XAG, NAS100) cruza umbral"
TFS = ("H1", "M30")

def grid():
    return [dict(n=n, m=m, stop_atr=s, trail=t, shorts=sh)
            for n, m, s, t, sh in itertools.product([48, 120, 240], [3, 4], [3.0, 5.0, 8.0], [False, True], [False, True])]

def signals(df, p):
    n = p["n"]
    def ret(sym):
        x = np.log(other(df, sym)); return x - x.shift(n)
    sc = (-np.sign(ret("USDX")) - np.sign(ret("USDJPY")) + np.sign(ret("XAGUSD")) - np.sign(ret("NAS100"))).fillna(0)
    prev = sc.shift(1).fillna(0)
    L = (sc >= p["m"]) & (prev < p["m"])
    S = ((sc <= -p["m"]) & (prev > -p["m"])) if p["shorts"] else (sc > 99)
    return dict(L=L.to_numpy(), S=S.to_numpy(), XL=(sc <= 0).to_numpy(), XS=(sc >= 0).to_numpy())
