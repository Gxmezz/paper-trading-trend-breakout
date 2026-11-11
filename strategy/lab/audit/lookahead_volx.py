"""Auditoría: señales de reg_volx con datos cortados vs completos (solo <= 2025-12-31)."""
import sys, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, reg_volx as R
df, i0 = E.load("2021-01-01", "2025-12-31", "M30")
assert df.index[-1] < pd.Timestamp("2026-01-01", tz="UTC")
rng = np.random.default_rng(1)
G = R.grid(); bad = 0
cuts = rng.integers(i0 + 500, len(df) - 10, 25)
for p in [G[i] for i in rng.integers(0, len(G), 6)] + [dict(vp=0.5, dist=0.0, don=10, stop_atr=2.0, trail=True, shorts=True)]:
    full = R.signals(df, p)
    for cidx in cuts:
        cut = df.iloc[:cidx]
        s = R.signals(cut, p)
        for k in ("L", "S", "XL", "XS"):
            m = (s[k] != full[k][:cidx]).sum()
            if m: bad += 1; print("MISMATCH", p, df.index[cidx], k, m, np.where(s[k] != full[k][:cidx])[0][-3:])
# también valores intermedios (vp, emas) en el último índice del corte
from ind import htf, atr, ema
for cidx in cuts[:10]:
    cut = df.iloc[:cidx]
    for nm, fn in [("vp", lambda x: R._pct(atr(x, 14))), ("e50", lambda x: ema(x.Close, 50))]:
        a = htf(cut, "4h", fn).values; b = htf(df, "4h", fn).values[:cidx]
        d = np.nanmax(np.abs(a - b)) if np.isfinite(a).any() else 0
        if d > 1e-9: print("VAL MISMATCH", nm, df.index[cidx], d)
print("cortes", len(cuts), "mismatches", bad)
