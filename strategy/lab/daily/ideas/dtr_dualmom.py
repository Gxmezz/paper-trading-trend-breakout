"""Momentum doble ajustado por volatilidad: con la tendencia de N meses a favor (ret N meses > 0), se ENTRA cuando
el momentum corto (ret de k sesiones / (vol diaria 60d * sqrt(k))) cruza por encima de z: reanudación de la tendencia
tras una pausa (pulso, no estado). Salida cuando el momentum corto se vuelve negativo o se pierde la tendencia larga."""
import itertools, numpy as np
NAME = "Momentum doble: tendencia N meses + reanudación del momentum corto ajustado por vol"

def grid():
    return [dict(n=n, k=k, z=z, stop_atr=sa, shorts=sh)
            for n, k, z, sa, sh in itertools.product([6, 12], [10, 21, 42], [0.0, 0.5], [2.0, 3.0, 5.0], [False, True])]

def signals(df, p):
    c = df.Close; lr = np.log(c).diff()
    vol = lr.rolling(60).std()
    lt = (c / c.shift(p["n"] * 21) - 1).to_numpy(copy=True)
    st = ((np.log(c) - np.log(c.shift(p["k"]))) / (vol * np.sqrt(p["k"]))).to_numpy(copy=True)
    stp = np.r_[np.nan, st[:-1]]
    ok = np.isfinite(lt) & np.isfinite(st) & np.isfinite(stp)
    z = p["z"]
    L = ok & (lt > 0) & (st > z) & (stp <= z)
    S = (ok & (lt < 0) & (st < -z) & (stp >= -z)) if p["shorts"] else np.zeros(len(c), bool)
    XL = ~ok | (st < 0) | (lt <= 0)
    XS = ~ok | (st > 0) | (lt >= 0)
    return dict(L=L, S=S, XL=XL, XS=XS)
