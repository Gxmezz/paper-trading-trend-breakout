import sys, json, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, judge as J, reg_volx as R
pd.set_option("display.width", 220)
G = R.grid(); d = pd.read_csv(J.OUT / "reg_volx_sem.csv"); x = d[d.tf == "M30"]
OOS = J.RESEARCH[2:]; old = [s for s in OOS if int(s[:4]) <= 2024]
w1 = J.walk(d, J.RESEARCH, "M30", 0.30, 1); w5 = J.walk(d, J.RESEARCH, "M30", 0.30, 5)
# top5 indices per semester (re-derive)
tr = x[x.cost == 0.20].pivot(index="idx", columns="sem")
def picks(j, top):
    a, b = J.RESEARCH[j-2], J.RESEARCH[j-1]
    gp, gl = tr["gp"][[a, b]].sum(axis=1), tr["gl"][[a, b]].sum(axis=1)
    ok = (tr["trades"][[a, b]].sum(axis=1) >= 12) & (gp > 1.1 * gl) & (gp > 0)
    sc = pd.concat([J.score(tr["monthly"][z], tr["maxDD"][z]) for z in (a, b)], axis=1).min(axis=1)[ok].sort_values(ascending=False)
    return list(sc.index[:top])
P5 = {s: picks(J.RESEARCH.index(s), 5) for s in OOS}
DATA = {s: E.load(*J.bounds(s), "M30") for s in OOS}
SIG = {}
def sim(s, k, cost):
    df, i0 = DATA[s]
    if (s, k) not in SIG: SIG[s, k] = R.signals(df, G[k])
    return E.simulate(df, i0, SIG[s, k], G[k], cost=cost)
print("== COSTES (selección intacta, coste 0,20) ==")
for c in (0.20, 0.30, 0.40, 0.50, 0.60):
    m1 = [sim(s, P5[s][0], c)["monthly"] for s in OOS]
    m5 = [np.mean([sim(s, k, c)["monthly"] for k in P5[s]]) for s in OOS]
    med = [np.median([sim(s, k, c)["monthly"] for k in range(len(G))]) for s in OOS] if c in (0.30, 0.50) else [np.nan]*len(OOS)
    o = [i for i, s in enumerate(OOS) if s in old]
    print(f"coste {c:.2f}: top1 21-24 {J.acum(np.array(m1)[o]):6.1f}  top5 {J.acum(np.array(m5)[o]):6.1f}  mediana {J.acum(np.array(med)[o]) if c in (0.30,0.50) else '':>6}  no_neg {(np.array(m1)>=0).sum()}/9  top1 2025 {J.acum(np.array(m1)[len(o):]):6.1f}")
print("== VECINDAD (OOS coste 0,30 de la elegida y de sus vecinas a 1 paso) ==")
vals = {"vp": [0.5, 0.7], "dist": [0.0, 1.0, 2.0], "don": [10, 20, 40], "stop_atr": [2.0, 3.0, 4.0], "shorts": [False, True]}
key = {json.dumps(g, sort_keys=True): i for i, g in enumerate(G)}
xm = x[x.cost == 0.30].set_index(["sem", "idx"]).monthly
rows = []
for s in OOS:
    k = P5[s][0]; g = G[k]; nb = []
    for f, vs in vals.items():
        j = vs.index(g[f])
        for jj in (j - 1, j + 1):
            if 0 <= jj < len(vs): h = dict(g); h[f] = vs[jj]; nb.append(key[json.dumps(h, sort_keys=True)])
    nm = [xm[s, n] for n in nb]
    rows.append(dict(sem=s, elegida=xm[s, k], vec_media=round(np.mean(nm), 2), vec_pos=f"{sum(v >= 0 for v in nm)}/{len(nm)}", grid_med=round(x[(x.cost==0.30)&(x["sem"]==s)].monthly.median(), 2), grid_pos=round((x[(x.cost==0.30)&(x["sem"]==s)].monthly >= 0).mean(), 2)))
r = pd.DataFrame(rows); print(r.to_string(index=False))
o = r["sem"].isin(old)
print("acum 21-24 elegida", J.acum(r[o].elegida), "vecinas", J.acum(r[o].vec_media), "grid mediana", J.acum(r[o].grid_med))
# grid completo fijo (sin selección): cada combinación acumulada 21H2-24H2
m = x[(x.cost == 0.30) & x["sem"].isin(old)].pivot(index="idx", columns="sem", values="monthly")
ac = m.apply(lambda r: J.acum(r.values), axis=1)
print("combinaciones fijas 21H2-24H2: % >0", round((ac > 0).mean()*100, 1), "mediana", ac.median(), "p10/p90", ac.quantile(.1), ac.quantile(.9))
gg = pd.DataFrame(G); gg["ac"] = ac.values
for f in ["vp", "dist", "don", "stop_atr", "shorts"]: print(f, gg.groupby(f).ac.median().round(1).to_dict())
