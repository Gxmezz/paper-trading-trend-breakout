"""Falsa ruptura (barrida) del máximo/mínimo del día anterior o del rango asiático: la vela perfora el nivel y CIERRA de
vuelta dentro -> operar hacia dentro. Stop por encima/debajo del extremo de la barrida (+ colchón ATR), objetivo tp_r o
salida al volver al centro del rango (XL/XS). Solo la primera barrida de cada lado por día, en horario Londres/NY."""
import itertools, numpy as np, pandas as pd
from ind import atr, ema, htf

NAME = "MR: barrida del máx/mín del día anterior o rango asiático"
TFS = ("M30", "H1")

def grid():
    G = []
    for lvl, win, ex, buf, flat in itertools.product(["pd", "asia"], ["lon", "lonny"], [1.0, 1.5, 2.0, "mid"],
                                                    [0.25, 0.5], [None, 0.5]):
        p = dict(level=lvl, win=win, buf=buf, flat=flat, max_bars=16, exit=ex)
        if ex != "mid": p["tp_r"] = ex
        G.append(p)
    return G

def _slope_h4(x):
    e = ema(x.Close, 50)
    return (e - e.shift(5)).abs() / atr(x, 14)

def _levels(df, kind):
    day = (df.index - pd.Timedelta(hours=22)).normalize()       # día de trading como el motor (corte 22 UTC)
    if kind == "pd":
        g = df.groupby(day).agg(H=("High", "max"), L=("Low", "min")).shift(1)   # día anterior COMPLETO
        H = pd.Series(g.H.reindex(day).values, df.index); L = pd.Series(g.L.reindex(day).values, df.index)
        valid = pd.Series(True, df.index)
    else:                                                         # rango asiático: velas que ABREN entre 22 y 07 UTC
        bar_min = int((df.index[1] - df.index[0]).total_seconds() // 60) if len(df) > 1 else 30
        close_t = df.index + pd.Timedelta(minutes=bar_min)
        hr = df.index.hour
        asia = (hr >= 22) | (hr < 7)
        hh = df.High.where(asia); ll = df.Low.where(asia)
        H = hh.groupby(day).cummax(); L = ll.groupby(day).cummin()
        H = H.groupby(day).ffill(); L = L.groupby(day).ffill()
        H = H.groupby(day).shift(1); L = L.groupby(day).shift(1)  # solo velas ya cerradas antes de la actual
        valid = pd.Series(~asia, df.index)
    return H, L, valid

def signals(df, p):
    o, h, l, c = df.Open, df.High, df.Low, df.Close
    H, L, valid = _levels(df, p["level"])
    hr = df.index.hour
    win = (hr >= 7) & (hr < (12 if p["win"] == "lon" else 17))
    a = atr(df, 14)
    S = (h > H) & (c < H) & valid & win
    Lg = (l < L) & (c > L) & valid & win
    if p["flat"] is not None:
        f = (htf(df, "4h", _slope_h4) < p["flat"]).fillna(False)
        S &= f; Lg &= f
    day = (df.index - pd.Timedelta(hours=22)).normalize()
    # solo la primera barrida de cada lado por día (contando también barridas sin cerrar dentro ya no: primera señal)
    S = S & (S.astype(int).groupby(day).cumsum() == 1)
    Lg = Lg & (Lg.astype(int).groupby(day).cumsum() == 1)
    stop_s = (h - c) + p["buf"] * a
    stop_l = (c - l) + p["buf"] * a
    stop = np.where(S, stop_s, np.where(Lg, stop_l, p["buf"] * a + (h - l)))
    out = dict(L=Lg.fillna(False).to_numpy(), S=S.fillna(False).to_numpy(), stop=np.asarray(stop, float))
    if p["exit"] == "mid":
        m = (H + L) / 2
        out["XL"] = (c >= m).fillna(False).to_numpy(); out["XS"] = (c <= m).fillna(False).to_numpy()
    return out
