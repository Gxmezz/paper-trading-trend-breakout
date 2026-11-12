"""Walk-forward del juez v2 en proceso, con tope de stop y coste variables (no toca ficheros del lab)."""
import sys, json, numpy as np, pandas as pd
from pathlib import Path
from multiprocessing import Pool
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, judge as J, sw_wkbo as W
SEMS = J.RESEARCH; COSTS = (0.20, 0.30, 0.40, 0.50)
_D = {}
def _init():
    for s in SEMS: _D[s] = E.load(*J.bounds(s), "H1")
def _job(a):
    cap, k = a; W.CAP_STOP = cap; p = W.grid()[k]; out = []
    for s in SEMS:
        df, i0 = _D[s]; sig = W.signals(df, p)
        for c in COSTS: out.append(dict(tf="H1", idx=k, sem=s, cost=c, cap=cap, **E.simulate(df, i0, sig, p, cost=c)))
    return out
def verdict_cost(d, cost):
    d2 = d.copy(); d2.loc[d2.cost == cost, "cost"] = -1; d2 = d2[d2.cost.isin([0.20, -1])]
    d2.loc[d2.cost == -1, "cost"] = 0.30          # truco: verdict() evalúa a 0,30; selección sigue a 0,20
    if cost == 0.20: d2 = pd.concat([d[d.cost == 0.20], d[d.cost == 0.20].assign(cost=0.30)])
    return J.verdict(d2, SEMS, "H1")
if __name__ == "__main__":
    caps = [float(x) for x in sys.argv[1:]] or [22.0]
    rows = []
    with Pool(2, initializer=_init) as pool:
        for rr in pool.imap_unordered(_job, [(c, k) for c in caps for k in range(len(W.grid()))], chunksize=3): rows += rr
    d = pd.DataFrame(rows); d.to_pickle(Path(__file__).parent / "wkbo_wf.pkl")
    pd.set_option("display.width", 250); out = []
    for cap in caps:
        dc = d[d.cap == cap]
        for c in COSTS:
            v, w1 = verdict_cost(dc, c); out.append(dict(cap=cap, cost=c, **{k: v[k] for k in ("acum_21_24_top1", "acum_21_24_top5", "acum_25_top1", "sem_no_neg", "trades", "peor_dd", "mediana_21_24", "APROBADO")}))
    print(pd.DataFrame(out).to_string(index=False))
