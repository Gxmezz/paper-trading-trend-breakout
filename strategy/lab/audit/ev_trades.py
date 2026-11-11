"""Auditoría ev_macro_drift H1: operaciones del walk-forward (largos/cortos, top-N, años, evento, fin de semana, costes, bootstrap)."""
import sys, json, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, judge as J, risk as K, ev_macro_drift as R
pd.set_option("display.width", 220)
TF = sys.argv[1] if len(sys.argv) > 1 else "H1"
G = R.grid(); d = pd.read_csv(J.OUT / "ev_macro_drift_sem.csv")
w = J.walk(d, J.RESEARCH, TF, 0.30, 1); w["p"] = [json.dumps(G[i]) if i >= 0 else "-" for i in w.idx]
print(w.to_string(index=False))
st, sem, tr, cur = K.oos_run("ev_macro_drift", TF, E.RiskConfig())
assert tr.t_out.max() < pd.Timestamp("2026-01-01", tz="UTC")
print(st)
tr.to_pickle(Path(__file__).parent / f"ev_drift_trades_{TF}.pkl")
nfp = set(pd.Timestamp(x).date() for x in R.NFP)
tr["ev"] = ["NFP" if t.tz_convert("America/New_York").date() in nfp else "FOMC" for t in tr.t_in]
tr["wknd"] = [any(x.weekday() == 5 for x in pd.date_range(i.normalize(), o.normalize())) for i, o in zip(tr.t_in, tr.t_out)]
tr["yr"] = tr.t_in.dt.year
print("\nn", len(tr), "win", (tr.R > 0).mean().round(3), "R medio", tr.R.mean().round(3), "mediana", tr.R.median().round(3), "sumR", tr.R.sum().round(1), "pnl€", tr.pnl.sum().round(0))
print(tr.sort_values("R", ascending=False).head(6)[["t_in", "t_out", "dir", "ev", "pnl", "R"]].to_string())
s = tr.R.sort_values(ascending=False)
print("sumR sin top1/3/5:", [round(s.iloc[k:].sum(), 1) for k in (1, 3, 5)], " R medio sin top3:", round(s.iloc[3:].mean(), 3))
for g in ("dir", "ev", "yr", "wknd"):
    print(tr.groupby(g).agg(n=("R", "size"), win=("R", lambda x: (x > 0).mean()), Rmed=("R", "mean"), sumR=("R", "sum"), pnl=("pnl", "sum")).round(2).to_string())
print(tr.groupby(["ev", "dir"]).agg(n=("R", "size"), Rmed=("R", "mean"), sumR=("R", "sum")).round(2).to_string())
# bootstrap del R medio
rng = np.random.default_rng(0); b = rng.choice(tr.R.values, (20000, len(tr))).mean(axis=1)
print("\nbootstrap R medio IC95", np.percentile(b, [2.5, 97.5]).round(3), "IC90", np.percentile(b, [5, 95]).round(3), "P(R<=0)", (b <= 0).mean().round(3))
t = tr.R.mean() / tr.R.std(ddof=1) * np.sqrt(len(tr)); print("t-stat", round(t, 2))
# Bonferroni aproximado: ~45 combinaciones idea×tf -> alfa 0,05/45 = 0,0011 (una cola)
from math import erf, sqrt
pv = 0.5 * (1 - erf(t / sqrt(2))); print("p una cola", round(pv, 4), " Bonferroni x45", round(min(1, pv * 45), 3))
