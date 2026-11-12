"""Cruce de medias SEMANALES del oro (cierres de semanas ya cerradas): largo mientras SMA rápida > SMA lenta,
corto (opcional) mientras rápida < lenta. Salida al perder el cruce (XL/XS); stop por ATR, con o sin trailing."""
import itertools, numpy as np
from dind import sma, period_closes, align_prev

NAME = "Cruce de medias semanales (estado), salida al perder el cruce"

def grid():
    return [dict(f=f, s=s, shorts=sh, stop_atr=k, trail=tr)
            for (f, s), sh, k, tr in itertools.product([(4, 13), (5, 20), (10, 30), (13, 52)], [False, True], [2.0, 3.0, 5.0], [False, True])]

def signals(df, p):
    wc = period_closes(df, "W")
    fa = align_prev(df, "W", sma(wc, p["f"])).to_numpy(copy=True)
    sl = align_prev(df, "W", sma(wc, p["s"])).to_numpy(copy=True)
    ok = np.isfinite(fa) & np.isfinite(sl)
    up = ok & (fa > sl); dn = ok & (fa < sl)
    S = dn if p["shorts"] else np.zeros(len(df), bool)
    return dict(L=up, S=S, XL=~up, XS=~dn)
