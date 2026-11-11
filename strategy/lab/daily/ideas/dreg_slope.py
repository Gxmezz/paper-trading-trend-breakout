"""Régimen por pendiente de la media semanal + freno por caída: largo si la media de N semanas sube respecto a la de
hace L semanas Y la caída desde el máximo de 252 días no supera X. Corto opcional si la pendiente es negativa.
Stop = pct del precio; renovación cada 21 velas."""
import itertools, numpy as np
from dind import sma, period_closes, align_prev

NAME = "Régimen: pendiente de media semanal + freno por caída"

def grid():
    return [dict(n=n, lb=lb, dd=dd, shorts=sh, stop_pct=sp, max_bars=21, stop_atr=1.0)
            for n, lb, dd, sh, sp in itertools.product([20, 40], [4, 13], [0.08, 0.12, 0.20, 1.0], [False, True], [0.05, 0.10])]

def signals(df, p):
    pc = period_closes(df, "W"); m = sma(pc, p["n"])
    rising = align_prev(df, "W", (m > m.shift(p["lb"])).astype(float).where(m.shift(p["lb"]).notna())) == 1
    falling = align_prev(df, "W", (m < m.shift(p["lb"])).astype(float).where(m.shift(p["lb"]).notna())) == 1
    ddn = (df.Close / df.Close.rolling(252, min_periods=200).max() - 1).to_numpy(copy=True)
    up = rising.to_numpy(copy=True) & (ddn > -p["dd"])
    dn = falling.to_numpy(copy=True)
    S = dn if p["shorts"] else np.zeros(len(df), bool)
    return dict(L=up, S=S, XL=~up, XS=~dn, stop=(p["stop_pct"] * df.Close).to_numpy(copy=True))
