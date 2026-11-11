"""Auditoría ev_macro_drift H1: misma combinación elegida por semestre (walk-forward del juez) y variantes:
costes 0,40/0,50, sin fin de semana, dirección al azar en las mismas velas, todo largo / todo corto, largos fijos tras cada evento."""
import sys, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, judge as J, ev_macro_drift as R
TF = sys.argv[1] if len(sys.argv) > 1 else "H1"; NR = int(sys.argv[2]) if len(sys.argv) > 2 else 400
G = R.grid(); d = pd.read_csv(J.OUT / "ev_macro_drift_sem.csv")
w = J.walk(d, J.RESEARCH, TF, 0.30, 1).set_index("sem"); OOS = list(w.index)
old = np.array([int(s[:4]) <= 2024 for s in OOS])
DATA = {s: E.load(*J.bounds(s), TF) for s in OOS}
assert all(DATA[s][0].index[-1] < pd.Timestamp("2026-01-01", tz="UTC") for s in OOS)

def run(make, cost=0.30):
    """make(df, p) -> sig; devuelve mensual por semestre y lista de R."""
    m, Rs = [], []
    for s in OOS:
        if w.loc[s, "idx"] < 0: m.append(0.0); continue
        df, i0 = DATA[s]; p = G[int(w.loc[s, "idx"])]; tr = []
        m.append(E.simulate(df, i0, make(df, p), p, cost=cost, trades=tr)["monthly"]); Rs += [t["R"] for t in tr]
    m = np.array(m); return dict(a2124=J.acum(m[old]), a25=J.acum(m[~old]), nn=int((m >= 0).sum()), n=len(Rs), Rm=round(np.mean(Rs), 3) if Rs else 0, sumR=round(np.sum(Rs), 1))

real = lambda df, p: R.signals(df, p)
def nowk(df, p):
    s = R.signals(df, p); X = s["XL"].copy(); ix = df.index
    gap = np.zeros(len(df), bool); gap[:-2] = (ix[2:] - ix[:-2]) > pd.Timedelta(hours=12)   # salir en la última vela antes del cierre
    X |= gap; return dict(L=s["L"], S=s["S"], XL=X, XS=X)
def fixed(dirn):
    def f(df, p):
        s = R.signals(df, p); e = s["L"] | s["S"]; return dict(L=e if dirn > 0 else 0 * e, S=e if dirn < 0 else 0 * e, XL=s["XL"], XS=s["XS"])
    return f
def every_event_long(df, p): return R.signals(df, dict(p, thr=0.0, mode=1)) | {} if False else _eel(df, p)
def _eel(df, p):
    s = R.signals(df, dict(p, thr=0.0)); e = s["L"] | s["S"]; return dict(L=e, S=np.zeros(len(e), bool), XL=s["XL"], XS=s["XS"])
rows = {}
for c in (0.30, 0.40, 0.50): rows[f"real coste {c}"] = run(real, c)
rows["real sin fin de semana"] = run(nowk); rows["sin fin de semana coste 0.50"] = run(nowk, 0.50)
rows["mismas velas, todo LARGO"] = run(fixed(1)); rows["mismas velas, todo CORTO"] = run(fixed(-1))
rows["LARGO tras cada evento (thr=0)"] = run(_eel)
rows["reversión (mode=-1)"] = run(lambda df, p: R.signals(df, dict(p, mode=-1)))
print(pd.DataFrame(rows).T.to_string())
rng = np.random.default_rng(11); sims = []
for k in range(NR):
    def rnd(df, p):
        s = R.signals(df, p); e = s["L"] | s["S"]; u = rng.random(len(e)) < 0.5
        return dict(L=e & u, S=e & ~u, XL=s["XL"], XS=s["XS"])
    sims.append(run(rnd))
S = pd.DataFrame(sims); r0 = rows["real coste 0.3"]
print(f"\nDirección al azar ({NR}): acum21-24 mediana {S.a2124.median():.1f} p90 {S.a2124.quantile(.9):.1f} p95 {S.a2124.quantile(.95):.1f}  P(azar>=real {r0['a2124']}) {(S.a2124 >= r0['a2124']).mean():.3f}")
print(f"  sumR mediana {S.sumR.median():.1f} p95 {S.sumR.quantile(.95):.1f}  P(azar>=real {r0['sumR']}) {(S.sumR >= r0['sumR']).mean():.3f};  sem no neg >= {r0['nn']}: {(S.nn >= r0['nn']).mean():.3f}")
