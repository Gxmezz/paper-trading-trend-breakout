"""MAR del examen 2025-26 frente a comprar y mantener (criterio 2 de EXAMEN_INDICES.md). Ejecutar DESPUÉS del examen.
Uso: LAB_EXAM=1 python strategy/lab/audit/ix_exam_mar.py <idea> <NDX|SPX>"""
import os, sys, importlib
from pathlib import Path
idea, asset = sys.argv[1], sys.argv[2].upper()
os.environ["LAB_ASSET"] = asset
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "daily")); sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "daily" / "ideas"))
import pandas as pd, numpy as np, engine as E, dind, djudge as J

d = pd.read_csv(J.OUT / f"{idea}_EXAM_year.csv")
d["year"] = d["year"].astype(str)
d = d[d.year.isin(["2022", "2023", "2024", "2025"])].copy(); d["year"] = d.year.astype(int)
picks = J.select(d, [2025]); G = importlib.import_module(idea).grid(); p = G[picks[2025][0]]
mod = importlib.import_module(idea)
for cost in ("base", "stress"):
    usd, pct = J.COSTS[cost]
    df, i0 = dind.load("2025-01-01", None); cv = []
    r = E.simulate(df, i0, mod.signals(df, p), p, cost=usd, rc=J.rc(pct), curve=cv)
    cur = pd.Series(cv, index=df.index[i0:i0 + len(cv)])
    cagr, dd, m = J.mar(cur)
    print(f"{idea} {asset} {cost}: rentabilidad {r['ret']:+.1f} % · operaciones {r['trades']} · CAGR {cagr:.2f} % · caída {dd:.1f} % · MAR {m:.2f}")
bh = J.buy_hold_period = None
df, i0 = dind.load("2025-01-01", None, warmup_days=0)
c = df.Close.iloc[i0:] * np.exp(np.arange(len(df) - i0) / 252 * np.log(1 + dind.CFG["div"] / 100))
print(f"comprar y mantener {asset} 2025-hoy: CAGR {J.mar(c)[0]:.2f} % · caída {J.mar(c)[1]:.1f} % · MAR {J.mar(c)[2]:.2f}")
print("parámetros elegidos con 2022-2024:", p)
