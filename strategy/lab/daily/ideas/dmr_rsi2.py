"""Reversión de varios días (Connors): RSI(2) diario muy bajo dentro de tendencia alcista (cierre > SMA larga) -> largo;
salida por rebote (cierre > SMA5 o RSI(2) > 70) o tras max_bars días; stop amplio en ATR. Cortos simétricos opcionales."""
import itertools
from dind import sma, rsi

NAME = "Reversión RSI(2) diaria en tendencia"

def grid():
    return [dict(th=t, ma=m, exit=x, stop_atr=k, max_bars=mb, shorts=sh)
            for t, m, x, k, mb, sh in itertools.product([5, 10, 15, 25], [100, 200], ["sma5", "rsi"], [2.0, 3.0, 5.0],
                                                        [5, 10], [False, True])]

def signals(df, p):
    c = df.Close; r = rsi(c, 2); t = sma(c, p["ma"]); s5 = sma(c, 5)
    L = (r < p["th"]) & (c > t)
    S = ((r > 100 - p["th"]) & (c < t)) if p["shorts"] else (c < -1)
    if p["exit"] == "sma5": XL, XS = c > s5, c < s5
    else: XL, XS = r > 70, r < 30
    return dict(L=L.to_numpy(copy=True), S=S.to_numpy(copy=True), XL=XL.to_numpy(copy=True), XS=XS.to_numpy(copy=True))
