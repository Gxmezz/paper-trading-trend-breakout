"""Intermercado 2: divergencia oro-plata con alcance. Si en las últimas n velas la plata ha subido con fuerza y el oro se ha
quedado atrás (z-score MÓVIL del diferencial de rentabilidades plata-oro > k), se compra oro esperando que alcance a la
plata; cortos al revés (plata cae fuerte, oro no). Salida: objetivo tp_r, stop ATR y salida por tiempo max_bars."""
import itertools, numpy as np
from ind import other

NAME = "Plata lidera, oro rezagado (z móvil del diferencial) -> oro alcanza"
TFS = ("H1", "M30")

def grid():
    return [dict(n=n, k=k, stop_atr=s, tp_r=t, max_bars=m)
            for n, k, s, t, m in itertools.product([6, 12, 24], [1.5, 2.0, 2.5], [1.5, 2.5], [1.5, 3.0, None], [12, 48])]

def signals(df, p):
    n = p["n"]
    g = np.log(df.Close); x = np.log(other(df, "XAGUSD"))
    rg = g - g.shift(n); rx = x - x.shift(n)
    d = rx - rg
    z = (d - d.rolling(1000, min_periods=300).mean()) / d.rolling(1000, min_periods=300).std()
    sx = rx / rx.rolling(1000, min_periods=300).std()
    L = (z > p["k"]) & (sx > 1.0)          # la plata sube de verdad (no solo el oro cae)
    S = (z < -p["k"]) & (sx < -1.0)
    return dict(L=L.fillna(False).to_numpy(), S=S.fillna(False).to_numpy())
