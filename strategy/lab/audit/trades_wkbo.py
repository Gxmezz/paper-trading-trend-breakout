import sys, json, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, judge as J, risk as K, sw_wkbo as R
pd.set_option("display.width", 220)
G = R.grid()
d = pd.read_csv(J.OUT / "sw_wkbo_sem.csv")
w = J.walk(d, J.RESEARCH, "H1", 0.30, 1)
w["p"] = [json.dumps(G[i]) if i >= 0 else "-" for i in w.idx]
print(w.to_string(index=False))
st, sem, tr, cur = K.oos_run("sw_wkbo", "H1", E.RiskConfig())
print(st); print(sem.to_string(index=False))
tr.to_pickle(Path(__file__).parent / "wkbo_trades.pkl")
tr["sem"] = tr.t_in.dt.year.astype(str) + "H" + np.where(tr.t_in.dt.month <= 6, "1", "2")
print("n", len(tr), "winrate", (tr.R > 0).mean().round(3), "mean R", tr.R.mean().round(3), "median R", tr.R.median().round(3))
print("R quantiles", tr.R.quantile([0, .1, .25, .5, .75, .9, .95, .99, 1]).round(2).to_dict())
print(tr.sort_values("R", ascending=False).head(8)[["t_in", "t_out", "dir", "pnl", "R"]].to_string())
old = tr[tr.t_in < "2025-01-01"]
for nm, x in [("todo", tr), ("21H2-24H2", old)]:
    s = x.pnl.sum(); top = x.pnl.sort_values(ascending=False)
    print(nm, "n", len(x), "pnl€", round(s, 1), "sin top1", round(s - top.iloc[:1].sum(), 1), "sin top3", round(s - top.iloc[:3].sum(), 1), "sin top5", round(s - top.iloc[:5].sum(), 1),
          "sumR", round(x.R.sum(), 1), "sumR sin top3", round(x.R.sum() - x.R.nlargest(3).sum(), 1))
print(tr.groupby("sem").agg(n=("R", "size"), sumR=("R", "sum"), pnl=("pnl", "sum")).round(1).to_string())
print(tr.groupby(tr.t_in.dt.year).agg(n=("R", "size"), sumR=("R", "sum"), pnl=("pnl", "sum")).round(1).to_string())
print(tr.groupby("dir").agg(n=("R", "size"), sumR=("R", "sum"), pnl=("pnl", "sum")).round(1).to_string())
