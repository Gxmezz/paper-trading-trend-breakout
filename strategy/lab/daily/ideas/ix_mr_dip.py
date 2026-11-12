"""Índices, reversión corta en largos tras una caída: disparador = N cierres bajistas seguidos (3/4), cierre bajo la
Bollinger inferior (20, 2) o mínimo de N días (5/10); opcional cierre > SMA200. Salida por rebote (SMA5, máximo anterior
o RSI(2) > 70) o tras max_bars días; stop amplio en ATR."""
import itertools
from dind import sma, rsi

NAME = "Índices: compra de caída (rachas / Bollinger / mínimo de N días)"

def grid():
    return [dict(trig=g, ma=m, exit=x, stop_atr=k, max_bars=mb)
            for g, m, x, k, mb in itertools.product(["down3", "down4", "bb2", "low5", "low10"], [0, 200],
                                                    ["sma5", "prevhigh", "rsi70"], [4.0, 8.0], [5, 10])]

def signals(df, p):
    c = df.Close; g = p["trig"]
    if g.startswith("down"):
        n = int(g[4:]); dn = (c < c.shift(1)).astype(int)
        L = dn.rolling(n).sum() >= n
    elif g == "bb2":
        L = c < sma(c, 20) - 2 * c.rolling(20).std()
    else:
        n = int(g[3:]); L = c <= c.rolling(n).min()
    if p["ma"]: L = L & (c > sma(c, p["ma"]))
    if p["exit"] == "sma5": XL = c > sma(c, 5)
    elif p["exit"] == "prevhigh": XL = c > df.High.shift(1)
    else: XL = rsi(c, 2) > 70
    S = c < -1
    return dict(L=L.to_numpy(copy=True), S=S.to_numpy(copy=True), XL=XL.to_numpy(copy=True))
