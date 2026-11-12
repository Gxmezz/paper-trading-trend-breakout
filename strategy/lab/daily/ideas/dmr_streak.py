"""Caída de varios días en tendencia alcista: N cierres bajistas seguidos o cierre bajo la banda inferior de Bollinger(20,k),
con cierre > SMA larga -> largo. Salida al cerrar sobre la media de 20 (banda media) o tras max_bars días; stop en ATR."""
import itertools
from dind import sma, bollinger

NAME = "Reversión: racha bajista / Bollinger en tendencia"

def grid():
    trig = [("streak", 3), ("streak", 4), ("streak", 5), ("bb", 1.5), ("bb", 2.0), ("bb", 2.5)]
    return [dict(trig=tr, v=v, ma=m, exit=x, stop_atr=k, max_bars=mb)
            for (tr, v), m, x, k, mb in itertools.product(trig, [100, 200], ["mid", "time"], [3.0, 5.0], [5, 10, 20])]

def signals(df, p):
    c = df.Close; t = sma(c, p["ma"]); lo, mid, _ = bollinger(c, 20, p["v"] if p["trig"] == "bb" else 2.0)
    if p["trig"] == "streak":
        dn = (c < c.shift(1)).astype(int); run = dn.rolling(int(p["v"])).sum() == p["v"]
    else:
        run = c < lo
    L = run & (c > t)
    XL = (c > mid) if p["exit"] == "mid" else (c < -1)
    return dict(L=L.to_numpy(copy=True), S=(c < -1).to_numpy(copy=True), XL=XL.to_numpy(copy=True))
