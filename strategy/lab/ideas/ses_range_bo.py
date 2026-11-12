"""Ruptura (o fallo) del rango de sesión: rango asiático -> Londres, rango Londres -> NY, rango de apertura NY (08-10).
Primera vela que cierra fuera del rango ya terminado, una operación por día, cierre a hora fija de NY.
Horas en NY desplazadas +5 h (el 'día' empieza a las 19:00 NY) para que el rango asiático no cruce la medianoche."""
import itertools, numpy as np, pandas as pd
from ind import atr, NY

NAME = "Sesión: ruptura de rango asiático/Londres/apertura NY con salida horaria"
TFS = ("M30", "H1")
# sesión: (inicio rango, fin rango, fin ventana de entrada, [horas de salida])  en horas NY + 5
SES = {"asia_lon": (0, 8, 13, (16, 21)),      # rango 19-03 NY, entrar 03-08, salir 11 o 16 NY
       "lon_ny":   (8, 13, 16, (19, 21)),     # rango 03-08 NY, entrar 08-11, salir 14 o 16 NY
       "ny_or":    (13, 15, 18, (19, 21))}    # rango 08-10 NY, entrar 10-13, salir 14 o 16 NY

def grid():
    G = []
    for s, (rs, re, te, xs) in SES.items():
        for xe, mode, f, tp, nar in itertools.product(xs, ["follow", "fade"], [0.5, 1.0], [None, 1.5, 3.0], [False, True]):
            G.append(dict(ses=s, xe=xe, mode=mode, f=f, tp_r=tp, narrow=nar))
    return G

def signals(df, p):
    rs, re, te, _ = SES[p["ses"]]; xe = p["xe"]
    mins = int((df.index[1] - df.index[0]).total_seconds() // 60) if len(df) > 1 else 30
    mins = 60 if mins >= 60 else 30
    t = df.index.tz_convert(NY) + pd.Timedelta(hours=5)
    key = pd.Series(t.year * 10000 + t.month * 100 + t.day, index=df.index)
    so = t.hour.values + t.minute.values / 60; sc = so + mins / 60
    inr = (so >= rs) & (so < re)
    H = df.High.where(inr); Lo = df.Low.where(inr)
    hi = H.groupby(key).transform("max"); lo = Lo.groupby(key).transform("min"); nb = H.groupby(key).transform("count")
    after = so >= re                          # solo se usa el rango cuando ya terminó
    need = (re - rs) * 60 / mins * 0.75
    w = hi - lo
    c = df.Close; a = atr(df)
    ok = after & (sc <= te) & (nb >= need).values & (w > 0).values
    if p["narrow"]:
        dw = w.where(after).groupby(key).first()                    # ancho del rango de cada día
        med = dw.shift(1).rolling(20, min_periods=10).median()      # mediana de los 20 días ANTERIORES
        ok &= (w < key.map(med)).values
    up = ok & (c > hi).values; dn = ok & (c < lo).values
    brk = pd.Series(up | dn, index=df.index)
    first = brk & (brk.astype(int).groupby(key).cumsum() == 1)
    up &= first.values; dn &= first.values
    if p["mode"] == "follow":
        L, S = up, dn
        dist = np.where(up, (c - hi) + p["f"] * w, (lo - c) + p["f"] * w)
    else:
        L, S = dn, up
        dist = p["f"] * w.values
    dist = np.maximum(np.nan_to_num(np.asarray(dist, float), nan=0.0), 0.5 * a.values)
    X = sc >= xe
    return dict(L=L, S=S, XL=X, XS=X, stop=dist)
