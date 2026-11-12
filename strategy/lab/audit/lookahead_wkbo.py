"""Auditoría sw_wkbo: señales y stop con datos cortados vs completos (<= 2025-12-31), H1. Comprueba también qué semana/día usa."""
import sys, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, sw_wkbo as W
df, i0 = E.load("2021-01-01", "2025-12-31", "H1")
assert df.index[-1] < pd.Timestamp("2026-01-01", tz="UTC")
rng = np.random.default_rng(3); G = W.grid(); bad = 0
cuts = np.r_[rng.integers(i0 + 300, len(df) - 5, 30)]
# añadir cortes en bordes delicados: viernes 20-21h, domingo 22-23h, lunes 0-2h UTC, y 21-23h cualquier día
t = df.index; edge = np.where(((t.day_of_week == 4) & (t.hour >= 19)) | ((t.day_of_week == 6) & (t.hour >= 22)) | ((t.day_of_week == 0) & (t.hour <= 2)) | (t.hour.isin([21, 22, 23])))[0]
edge = edge[edge > i0 + 300]; cuts = np.r_[cuts, rng.choice(edge, 30, replace=False)]
ps = [G[i] for i in rng.integers(0, len(G), 6)] + [g for g in G if g["filt"] == "e20" and g["ex"] == "mid" and g["shorts"]][:1]
for p in ps:
    full = W.signals(df, p)
    for cidx in cuts:
        s = W.signals(df.iloc[:cidx], p)
        for k in ("L", "S", "XL", "XS", "stop"):
            a, b = np.asarray(s[k]), np.asarray(full[k])[:cidx]
            m = (~np.isclose(a, b, equal_nan=True)).sum() if k == "stop" else (a != b).sum()
            if m: bad += 1; print("MISMATCH", p, t[cidx], k, m)
print("cortes", len(cuts), "x combos", len(ps), "mismatches", bad)
# comprobación semántica: qué máximo semanal ve cada vela
wh = W.T(df, "1W", lambda x: x.High)
for ts in ["2024-03-08 20:00", "2024-03-10 22:00", "2024-03-11 09:00", "2024-03-15 20:00", "2024-03-17 23:00"]:
    i = df.index.get_loc(pd.Timestamp(ts, tz="UTC"))
    print(ts, "UTC  wh visto =", wh[i])
for a, b in [("2024-03-03 22:00", "2024-03-08 22:00"), ("2024-03-10 22:00", "2024-03-15 22:00")]:
    print("max real semana", a, "->", b, df[pd.Timestamp(a, tz="UTC"):pd.Timestamp(b, tz="UTC") - pd.Timedelta(hours=1)].High.max())
d = W.T(df, "1D", lambda x: x.Close)
for ts in ["2024-03-12 20:00", "2024-03-12 22:00", "2024-03-12 23:00"]:
    i = df.index.get_loc(pd.Timestamp(ts, tz="UTC")); print(ts, "cierre D visto", d[i], " close H1 de esa vela", df.Close.iloc[i])
