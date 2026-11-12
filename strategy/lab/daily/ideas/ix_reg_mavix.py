"""Régimen índices: largo si el cierre está sobre su media (100/200 días o 10 meses) Y el VIX no está alto
(nivel fijo 25/30/35 o bajo su percentil 80/90 de los últimos 252 días; VIX desconocido = no filtra).
Se sale (XL) cuando falla cualquiera. Opcional: corto bajo la media. Stop amplio k×ATR14 (tamaño ∝ 1/volatilidad);
max_bars=42 renueva la posición (y su tamaño) cada ~2 meses."""
import itertools, numpy as np
from dind import sma, period_closes, align_prev, vix

NAME = "Régimen índices: media larga + VIX"

def grid():
    return [dict(ma=m, vix=v, shorts=sh, stop_atr=k, max_bars=42)
            for m, v, sh, k in itertools.product(["d100", "d200", "m10"], [None, 25, 30, 35, "p80", "p90"],
                                                 [False, True], [5.0, 8.0])]

def trend_ma(df, ma):
    if ma == "m10": return align_prev(df, "M", sma(period_closes(df, "M"), 10))
    return sma(df.Close, int(ma[1:]))

def vix_ok(df, v):
    if v is None: return np.ones(len(df), bool)
    x = vix(df)
    if isinstance(v, str):
        q = int(v[1:]) / 100
        thr = x.rolling(252, min_periods=120).quantile(q)      # percentil con ventana pasada (incluye hoy, conocido al cierre)
        ok = x <= thr
        return (ok | thr.isna() | x.isna()).to_numpy(copy=True)
    return ((x < v) | x.isna()).to_numpy(copy=True)

def signals(df, p):
    m = trend_ma(df, p["ma"]); c = df.Close
    up = (c > m).to_numpy(copy=True); dn = (c < m).to_numpy(copy=True)
    good = up & vix_ok(df, p["vix"])
    S = dn if p["shorts"] else np.zeros(len(df), bool)
    return dict(L=good, S=S, XL=~good, XS=~dn)
