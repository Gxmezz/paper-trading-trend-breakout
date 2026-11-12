"""Régimen índices con histéresis: dentro cuando el cierre supera la media (150/200 días o 10 meses) en +b, fuera cuando
cae por debajo en -b (estado que solo usa el pasado). Además, salida de pánico si el VIX supera k veces su media
de 50 días (pico de miedo); se vuelve a entrar cuando el pico remite y el estado sigue 'dentro'.
Opcional: corto con estado 'fuera'. Stop k×ATR14, max_bars=42."""
import itertools, numpy as np
from dind import sma, period_closes, align_prev, vix

NAME = "Régimen índices: media con banda + salida por pico de VIX"

def grid():
    return [dict(ma=m, b=b, spike=k, shorts=sh, stop_atr=sa, max_bars=42)
            for m, b, k, sh, sa in itertools.product(["d150", "d200", "m10"], [0.0, 0.02, 0.04], [1.3, 1.5, 1.8, None],
                                                     [False, True], [5.0, 8.0])]

def signals(df, p):
    c = df.Close
    m = align_prev(df, "M", sma(period_closes(df, "M"), 10)) if p["ma"] == "m10" else sma(c, int(p["ma"][1:]))
    cc = c.to_numpy(copy=True); mm = m.to_numpy(copy=True); n = len(cc)
    st = np.zeros(n, bool); s = False
    for i in range(n):
        if np.isfinite(mm[i]):
            if not s and cc[i] > mm[i] * (1 + p["b"]): s = True
            elif s and cc[i] < mm[i] * (1 - p["b"]): s = False
        st[i] = s
    if p["spike"] is None: calm = np.ones(n, bool)
    else:
        x = vix(df); calm = ((x <= p["spike"] * sma(x, 50)) | x.isna() | sma(x, 50).isna()).to_numpy(copy=True)
    good = st & calm; out = ~st
    S = out if p["shorts"] else np.zeros(n, bool)
    return dict(L=good, S=S, XL=~good, XS=~out)
