"""Swing: momentum de N días (20/40/60). Largo si el cierre D supera al de hace N días (y opcionalmente a la EMA20 D);
corto al revés. Entrada al empezar el día (H1), se re-entra tras un stop si el momentum sigue. Salida al perder el
momentum (cierre D vuelve al otro lado), stop = min(k x ATR14 D, 22 $) y trailing amplio opcional."""
import itertools, numpy as np, pandas as pd
from ind import ema, atr, htf

NAME = "Swing: momentum de N días con salida por pérdida de momentum"
TFS = ("H1",)
CAP_STOP = 22.0   # ajuste 1: tope 32 -> 22 $ (con 32 $, tras una pérdida 3 % del capital < 32 $ y el motor salta todas las entradas)
SH = pd.Timedelta(hours=2)

def D(df, fn):
    return htf(df.set_axis(df.index + SH), "1D", fn).to_numpy(copy=True)

def grid():
    return [dict(n=n, conf=cf, k=k, shorts=sh, **(dict(trail=True, trail_atr=tr) if tr else {}))
            for n, cf, k, tr, sh in itertools.product([20, 40, 60], [False, True], [1.0, 1.5], [None, 10.0], [False, True])]

def signals(df, p):
    n = p["n"]
    c = D(df, lambda x: x.Close); m = D(df, lambda x: x.Close - x.Close.shift(n)); e = D(df, lambda x: ema(x.Close, 20))
    a = D(df, lambda x: atr(x, 14))
    new = np.r_[False, c[1:] != c[:-1]]
    bull = m > 0; bear = m < 0
    if p["conf"]: bull, bear = bull & (c > e), bear & (c < e)
    L = new & bull
    S = (new & bear) if p["shorts"] else np.zeros(len(df), bool)
    return dict(L=L, S=S, XL=new & ~(m > 0), XS=new & ~(m < 0), stop=np.minimum(p["k"] * a, CAP_STOP))
