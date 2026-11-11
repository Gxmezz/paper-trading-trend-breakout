"""Momentum de series temporales (TSMOM) del oro: estar largo mientras la rentabilidad de los últimos N meses
es > 0 (y corto si < 0, opcional). Salida cuando se pierde el momentum (XL/XS), stop ancho por ATR solo de catástrofe.
'monthly' = la señal se recalcula con cierres MENSUALES cerrados (rebalanceo mensual, menos ruido);
'daily' = rentabilidad de N*21 sesiones al cierre de cada día. El tamaño por riesgo/ATR ya ajusta por volatilidad."""
import itertools, numpy as np
from dind import period_closes, align_prev

NAME = "TSMOM N meses (largo si ret>0, salida al perder momentum)"

def grid():
    return [dict(n=n, mode=m, shorts=sh, stop_atr=k)
            for n, m, sh, k in itertools.product([3, 6, 9, 12], ["monthly", "daily"], [False, True], [3.0, 5.0, 8.0])]

def signals(df, p):
    c = df.Close
    if p["mode"] == "monthly":
        mc = period_closes(df, "M"); r = align_prev(df, "M", mc / mc.shift(p["n"]) - 1)
    else:
        r = c / c.shift(p["n"] * 21) - 1
    r = r.to_numpy(copy=True); ok = np.isfinite(r)
    up = ok & (r > 0); dn = ok & (r < 0)
    L = up; XL = ~up
    S = dn if p["shorts"] else np.zeros(len(c), bool); XS = ~dn
    return dict(L=L, S=S, XL=XL, XS=XS)
