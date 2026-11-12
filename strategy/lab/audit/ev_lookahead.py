"""Auditoría ev_macro_drift: señales con datos cortados = datos completos (solo <= 2025-12-31), H1 y M30."""
import sys, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, ev_macro_drift as R
G = R.grid(); rng = np.random.default_rng(3)
for tf in ("H1", "M30"):
    df, i0 = E.load("2020-07-01", "2025-12-31", tf); assert df.index[-1] < pd.Timestamp("2026-01-01", tz="UTC")
    # cortes justo alrededor de eventos (lo más delicado) + al azar
    ev = [e for e in R.events("both") if df.index[0] < e < df.index[-1]]
    near = [int(df.index.searchsorted(e)) + k for e in rng.choice(ev, 25, replace=False) for k in (0, 1, 2, 3)]
    cuts = sorted(set(near + list(rng.integers(i0 + 50, len(df) - 5, 20))))
    bad = 0; tot = 0
    for p in [G[i] for i in rng.integers(0, len(G), 8)]:
        full = R.signals(df, p)
        for c in cuts:
            s = R.signals(df.iloc[:c], p); tot += 1
            for k in ("L", "S"):          # entradas: deben coincidir exactamente
                m = (s[k] != full[k][:c]).sum()
                if m: bad += 1; print("MISMATCH", tf, p, df.index[c], k, m)
            # salidas: en el corte puede faltar una salida futura (x >= n) -> solo se comprueba que no aparezcan salidas extra
            extra = (s["XL"] & ~full["XL"][:c]).sum()
            if extra: bad += 1; print("EXIT EXTRA", tf, p, df.index[c], extra)
    print(tf, "cortes x combos", tot, "mismatches", bad)
