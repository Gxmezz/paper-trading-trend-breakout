"""Swing: ruptura del rango de la semana anterior. Cierre H1 que cruza el máximo (mínimo) de la semana cerrada, con filtro
opcional de tendencia (EMA D 20/50). Stop = min(k x ATR14 D, 22 $). Salida: trailing amplio por ATR H1, vuelta al
punto medio de la semana anterior, u objetivo 2R/3R; como mucho 10 días."""
import itertools, numpy as np, pandas as pd
from ind import ema, atr, htf

NAME = "Swing: ruptura del rango semanal previo + filtro EMA D"
TFS = ("H1",)
CAP_STOP = 22.0   # ajuste 1 (tras ver C = -22 %): tope de stop 32 -> 22 $ (1 oz arriesga <= ~2 % en vez de ~3 %)
SH = pd.Timedelta(hours=2)   # 22:00 UTC -> 00:00 (día/semana de trading del oro)

def T(df, rule, fn):
    return htf(df.set_axis(df.index + SH), rule, fn).to_numpy(copy=True)

EX = {"tr6": dict(trail=True, trail_atr=6.0), "tr10": dict(trail=True, trail_atr=10.0), "mid": {},
      "tp2": dict(tp_r=2.0), "tp3": dict(tp_r=3.0)}

def grid():
    return [dict(filt=f, k=k, ex=x, shorts=sh, max_bars=230, **EX[x])
            for f, k, x, sh in itertools.product(["none", "e20", "e50"], [0.75, 1.0, 1.5], list(EX), [False, True])]

def signals(df, p):
    c = df.Close.to_numpy(copy=True); pc = np.r_[np.nan, c[:-1]]
    wh = T(df, "1W", lambda x: x.High); wl = T(df, "1W", lambda x: x.Low); mid = (wh + wl) / 2
    a = T(df, "1D", lambda x: atr(x, 14)); cd = T(df, "1D", lambda x: x.Close)
    if p["filt"] == "none": bull = bear = np.ones(len(df), bool)
    else:
        n = 20 if p["filt"] == "e20" else 50
        e = T(df, "1D", lambda x: ema(x.Close, n)); bull, bear = cd > e, cd < e
    L = bull & (c > wh) & (pc <= wh)
    S = (bear & (c < wl) & (pc >= wl)) if p["shorts"] else np.zeros(len(df), bool)
    if p["ex"] == "mid": XL, XS = c < mid, c > mid
    else: XL = XS = np.zeros(len(df), bool)
    return dict(L=L, S=S, XL=XL, XS=XS, stop=np.minimum(p["k"] * a, CAP_STOP))
