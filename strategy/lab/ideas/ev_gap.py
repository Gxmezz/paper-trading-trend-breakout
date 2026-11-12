"""Hueco de apertura del domingo (reapertura tras el fin de semana). gap = open de la 1.ª vela de la semana - cierre del viernes.
Si |gap| >= thr×ATR14 (ATR del viernes, sin futuro), al cierre de la vela `wait` de la semana se entra en contra del hueco
(mode=-1: apuesta a que se rellena; salida al tocar el cierre del viernes, vía objetivo/XL) o a favor (mode=1: continuación).
Salida extra tras `hold` horas o stop ATR."""
import itertools, numpy as np, pandas as pd

NAME = "Eventos: hueco de apertura semanal (relleno o continuación)"
TFS = ("M30", "H1")

def grid():
    return [dict(thr=t, mode=m, wait=w, hold=h, stop_atr=s)
            for t, m, w, h, s in itertools.product([0.0, 0.25, 0.5, 1.0], [-1, 1], [1, 2, 4], [6, 24, 72], [1.5, 3.0])]

def _atr(df, n=14):
    pc = df.Close.shift()
    tr = pd.concat([df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean().to_numpy()

def signals(df, p):
    n = len(df); idx = df.index
    step = idx.to_series().diff().mode().iloc[0]
    o = df.Open.to_numpy(); c = df.Close.to_numpy(); a = _atr(df)
    gapdt = idx.to_series().diff().to_numpy()
    first = np.where(gapdt > np.timedelta64(36, "h"))[0]          # 1.ª vela tras el cierre del fin de semana
    L = np.zeros(n, bool); S = np.zeros(n, bool); XL = np.zeros(n, bool); XS = np.zeros(n, bool)
    for f in first:
        fc = c[f - 1]; g = o[f] - fc; s = f + p["wait"] - 1
        if s >= n - 1 or abs(g) < p["thr"] * a[f - 1] or g == 0: continue
        if idx[s] - idx[f] > pd.Timedelta(hours=p["wait"] * step / pd.Timedelta(hours=1) + 2): continue
        d = -np.sign(g) if p["mode"] == -1 else np.sign(g)
        if p["mode"] == -1 and (c[s] - fc) * np.sign(g) <= 0: continue   # ya rellenado antes de entrar
        (L if d > 0 else S)[s] = True
        end = idx[s] + step + pd.Timedelta(hours=p["hold"])
        for j in range(s + 1, n):
            if idx[j] + step >= end: (XL if d > 0 else XS)[j] = True; break
            if p["mode"] == -1 and ((d > 0 and c[j] >= fc) or (d < 0 and c[j] <= fc)):   # hueco rellenado (al cierre)
                (XL if d > 0 else XS)[j] = True; break
    return dict(L=L, S=S, XL=XL, XS=XS)
