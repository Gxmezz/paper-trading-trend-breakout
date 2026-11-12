"""Swing: retroceso en tendencia diaria. Tendencia = EMA D subiendo (5 días) y cierre D > EMA - 1 ATR D (simétrico en cortos). Setup al
cierre diario: RSI(2) D extremo o N cierres D seguidos contra la tendencia. Entrada al empezar el día siguiente (H1).
Stop = min(k x ATR14 D, 22 $) (1 oz). Salida: cierre D por encima del máximo D previo / 5 días / objetivo 2R."""
import itertools, numpy as np, pandas as pd
from ind import ema, rsi, atr, htf

NAME = "Swing: retroceso diario (RSI2 / N cierres) en tendencia EMA D"
TFS = ("H1",)
CAP_STOP = 22.0   # ajuste 1: tope 32 -> 22 $ (con 32 $, tras una pérdida 3 % del capital < 32 $ y el motor salta todas las entradas)
SH = pd.Timedelta(hours=2)   # 22:00 UTC -> 00:00: día de trading del oro (sin vela de domingo suelta)

def D(df, fn):
    return htf(df.set_axis(df.index + SH), "1D", fn).to_numpy(copy=True)

def grid():
    return [dict(setup=s, ema=e, k=k, ex=x, shorts=sh, **({"tp_r": 2.0} if x == "tp2" else {}),
                 **({"max_bars": 115} if x != "hi" else {}))
            for s, e, k, x, sh in itertools.product(["rsi10", "rsi25", "dn2", "dn3"], [20, 50], [0.75, 1.25],
                                                     ["hi", "d5", "tp2"], [False, True])]

def signals(df, p):
    c = D(df, lambda x: x.Close); hi = D(df, lambda x: x.High.shift(1)); lo = D(df, lambda x: x.Low.shift(1))
    e = D(df, lambda x: ema(x.Close, p["ema"])); e5 = D(df, lambda x: ema(x.Close, p["ema"]).shift(5))
    a = D(df, lambda x: atr(x, 14)); r = D(df, lambda x: rsi(x.Close, 2))
    up3 = D(df, lambda x: (x.Close > x.Close.shift()).astype(float).rolling(3).sum())   # cierres D alcistas en 3 días
    dn3 = D(df, lambda x: (x.Close < x.Close.shift()).astype(float).rolling(3).sum())
    up2 = D(df, lambda x: (x.Close > x.Close.shift()).astype(float).rolling(2).sum())
    dn2 = D(df, lambda x: (x.Close < x.Close.shift()).astype(float).rolling(2).sum())
    new = np.r_[False, c[1:] != c[:-1]]                                       # primera vela H1 con el diario nuevo
    s = p["setup"]
    if s == "rsi10": pl, ps = r < 10, r > 90
    elif s == "rsi25": pl, ps = r < 25, r > 75
    elif s == "dn2": pl, ps = dn2 >= 2, up2 >= 2
    else: pl, ps = dn3 >= 3, up3 >= 3
    # tendencia: EMA D subiendo y el cierre no más de 1 ATR D por debajo (el retroceso puede perforar la EMA)
    bull = (e > e5) & (c > e - a); bear = (e < e5) & (c < e + a)
    L = new & bull & pl
    S = (new & bear & ps) if p["shorts"] else np.zeros(len(df), bool)
    stop = np.minimum(p["k"] * a, CAP_STOP)
    if p["ex"] == "hi": XL, XS = new & (c > hi), new & (c < lo)
    else: XL = XS = np.zeros(len(df), bool)
    return dict(L=L, S=S, XL=XL, XS=XS, stop=stop)
