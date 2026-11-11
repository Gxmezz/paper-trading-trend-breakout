"""Régimen tendencia + volatilidad: largo si el precio está sobre la media de W semanas Y la volatilidad de 20 días
no supera k veces su mediana del último año (sin mirar al futuro). Se sale cuando falla cualquiera de las dos.
Opcional: corto si está bajo la media y la volatilidad es normal. Stop = pct del precio; renovación cada 21 velas."""
import itertools, numpy as np
from dind import sma, period_closes, align_prev

NAME = "Régimen: media semanal + filtro de volatilidad"

def grid():
    return [dict(w=w, k=k, shorts=sh, stop_pct=sp, max_bars=21, stop_atr=1.0)
            for w, k, sh, sp in itertools.product([26, 40], [1.25, 1.5, 2.0, 99.0], [False, True], [0.05, 0.10, 0.20])]

def signals(df, p):
    pc = period_closes(df, "W"); mm = align_prev(df, "W", sma(pc, p["w"]))
    r = np.log(df.Close).diff(); v = r.rolling(20).std(); vm = v.rolling(252).median()
    calm = (v <= p["k"] * vm).to_numpy(copy=True)
    up = (df.Close > mm).to_numpy(copy=True) & calm
    dn = (df.Close < mm).to_numpy(copy=True) & calm
    S = dn if p["shorts"] else np.zeros(len(df), bool)
    return dict(L=up, S=S, XL=~up, XS=~dn, stop=(p["stop_pct"] * df.Close).to_numpy(copy=True))
