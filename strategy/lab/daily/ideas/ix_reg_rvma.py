"""Régimen índices: volatilidad realizada (20 días) en percentil alto de su propia historia móvil (252 o 756 días
pasados) combinada con la media de 100/200 días. mode='and': largo solo si calma Y sobre la media; mode='or': largo
salvo que haya volatilidad alta Y precio bajo la media (peligro = ambas). Opcional: corto en peligro y bajo la media.
Stop k×ATR14, max_bars=42."""
import itertools, numpy as np
from dind import sma

NAME = "Régimen índices: percentil de volatilidad realizada + media"

def grid():
    return [dict(q=q, win=w, ma=m, mode=md, shorts=sh, stop_atr=k, max_bars=42)
            for q, w, m, md, sh, k in itertools.product([0.6, 0.7, 0.8, 0.9], [252, 756], [100, 200], ["and", "or"],
                                                         [False, True], [5.0, 8.0])]

def signals(df, p):
    c = df.Close; r = np.log(c).diff(); rv = r.rolling(20).std()
    pct = rv.rolling(p["win"], min_periods=200).rank(pct=True)     # rango de hoy dentro de la ventana pasada
    calm = ((pct <= p["q"]) | pct.isna()).to_numpy(copy=True)
    m = sma(c, p["ma"]); up = (c > m).to_numpy(copy=True)
    good = (calm & up) if p["mode"] == "and" else (calm | up)
    bad = ~good & ~up                                                # corto solo en peligro bajo la media
    S = bad if p["shorts"] else np.zeros(len(df), bool)
    return dict(L=good, S=S, XL=~good, XS=~bad)
