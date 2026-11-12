"""Índices, reversión corta en largos (Connors): RSI(2) muy bajo con el cierre sobre la media larga -> largo al open.
Salida por rebote (cierre > SMA5, RSI(2) > 70 o cierre > máximo del día anterior) o tras max_bars días; stop amplio en ATR."""
import itertools
from dind import sma, rsi

NAME = "Índices: RSI(2) bajo en tendencia alcista (solo largos)"

def grid():
    return [dict(th=t, ma=m, exit=x, stop_atr=k, max_bars=mb)
            for t, m, x, k, mb in itertools.product([5, 10, 15, 25], [100, 200], ["sma5", "rsi70", "prevhigh"],
                                                    [3.0, 6.0, 10.0], [5, 10])]

def signals(df, p):
    c = df.Close; r = rsi(c, 2); t = sma(c, p["ma"])
    L = (r < p["th"]) & (c > t)
    if p["exit"] == "sma5": XL = c > sma(c, 5)
    elif p["exit"] == "rsi70": XL = r > 70
    else: XL = c > df.High.shift(1)
    S = c < -1
    return dict(L=L.to_numpy(copy=True), S=S.to_numpy(copy=True), XL=XL.to_numpy(copy=True))
