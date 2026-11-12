"""Efecto cambio de mes: los flujos de fin/principio de mes (nóminas, aportaciones a fondos/ETF, rebalanceos) -> largo
desde el cierre del primer día con día del mes >= d0 (calendario, sin mirar al futuro) y salida tras max_bars días.
Filtro opcional de tendencia (cierre > SMA200). Pocas combinaciones a propósito."""
import itertools, numpy as np, pandas as pd
from dind import sma

NAME = "Cambio de mes (turn of month)"

def grid():
    return [dict(d0=d, max_bars=mb, trend=tr, stop_atr=k)
            for d, mb, tr, k in itertools.product([22, 25, 27], [3, 5, 8], [False, True], [3.0, 5.0])]

def signals(df, p):
    c = df.Close; dt = pd.DatetimeIndex(df.date); dom = dt.day.values; per = dt.to_period("M")
    first = (dom >= p["d0"]) & ~pd.Series(dom >= p["d0"]).groupby(per).shift(1, fill_value=False).to_numpy(bool)
    L = first & ((c > sma(c, 200)).to_numpy(bool) if p["trend"] else True)
    return dict(L=np.asarray(L, bool).copy(), S=np.zeros(len(df), bool))
