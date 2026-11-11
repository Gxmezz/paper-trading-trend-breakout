"""
Walk-forward con XAUUSD real (M30 del MT5 de Vantage, 2020-2026) para H1 y M30.

1. Simula las 1632 combinaciones de vantage_opt.grid() en cada semestre por separado (con 30 días de calentamiento).
2. Para operar el semestre S elige la combinación SOLO con los 2 semestres anteriores (mismo criterio que
   vantage_opt: el peor de los dos periodos, retorno mensual / caída; ≥15 operaciones y PF > 1,1 en ambos).
3. El resultado de S es fuera de muestra: la combinación elegida nunca vio esos datos.

Descargar antes los datos: strategy/data/XAUUSD_mt5_M30.pkl (MT5 → XAUUSD M30, hora convertida a UTC).
Uso: python strategy/walkforward.py            (simula y guarda results/wf_sem_{TF}.csv)
     python strategy/walkforward.py --report   (solo el informe, con los CSV ya calculados)
"""
import sys, numpy as np, pandas as pd
from multiprocessing import Pool
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vantage_opt as vo

R = Path(__file__).parent / "results"
F = "XAUUSD_mt5_M30.pkl"
SEMS = [f"{y}H{h}" for y in range(2020, 2027) for h in (1, 2)]
SEMS = [s for s in SEMS if s != "2020H1"]          # 2020H1 no tiene calentamiento previo
TFS = ("H1", "M30")
COSTS = (0.20, 0.30)

def bounds(s):
    y, h = int(s[:4]), s[-1]
    return (f"{y}-01-01", f"{y}-06-30") if h == "1" else (f"{y}-07-01", None if y == 2026 else f"{y}-12-31")

for s in SEMS: vo.PERIODS[s] = (F, *bounds(s))
DATA = {}

def init(tf):
    for s in SEMS: DATA[s] = vo.load(s, tf)

def job(args):
    tf, k, p = args
    out = []
    for s in SEMS:
        df, i0 = DATA[s]
        for c in COSTS:
            r = vo.simulate(df, i0, p, cost_side=c)
            out.append(dict(tf=tf, idx=k, sem=s, cost=c, **r))
    return out

def simulate_all():
    G = vo.grid()
    for tf in TFS:
        with Pool(8, initializer=init, initargs=(tf,)) as pool:
            rows = [r for rr in pool.imap_unordered(job, [(tf, k, p) for k, p in enumerate(G)], chunksize=8) for r in rr]
        pd.DataFrame(rows).to_csv(R / f"wf_sem_{tf}.csv", index=False)
        print(tf, "simulado", flush=True)

def score(d): return d.monthly / d.maxDD.abs().clip(lower=5)

def walk(tf, top=1):
    d = pd.read_csv(R / f"wf_sem_{tf}.csv")
    tr = d[d.cost == 0.20].pivot(index="idx", columns="sem")
    rows = []
    for j in range(2, len(SEMS)):
        a, b, s = SEMS[j - 2], SEMS[j - 1], SEMS[j]
        ok = (tr["trades"][[a, b]].min(axis=1) >= 15) & (tr["PF"][[a, b]].min(axis=1) > 1.1)
        sc = pd.concat([score(pd.DataFrame({"monthly": tr["monthly"][x], "maxDD": tr["maxDD"][x]})) for x in (a, b)], axis=1).min(axis=1)
        sc = sc[ok].sort_values(ascending=False)
        for c in COSTS:
            te = d[(d["sem"] == s) & (d.cost == c)].set_index("idx")
            if len(sc) == 0:
                rows.append(dict(tf=tf, sem=s, cost=c, n_ok=0, idx=-1, monthly=0.0, maxDD=0.0, trades=0)); continue
            pick = sc.index[:top]
            rows.append(dict(tf=tf, sem=s, cost=c, n_ok=len(sc), idx=int(pick[0]), train_score=round(sc.iloc[0], 2),
                             monthly=round(te.loc[pick, "monthly"].mean(), 2), maxDD=round(te.loc[pick, "maxDD"].mean(), 1),
                             trades=int(te.loc[pick, "trades"].mean()),
                             # referencia: media de TODAS las combinaciones (lo que darías eligiendo al azar)
                             all_mean=round(te["monthly"].mean(), 2)))
    return pd.DataFrame(rows)

def compound(m): return round((np.prod(1 + np.asarray(m) / 100) ** 6 - 1) * 100, 1)

def report():
    G = vo.grid(); pd.set_option("display.width", 220)
    out = []
    for top in (1, 5):
        w = pd.concat([walk(tf, top) for tf in TFS])
        if top == 1:
            sel = w[w.cost == 0.20].copy()
            sel["params"] = [str({k: v for k, v in G[i].items()}) if i >= 0 else "-" for i in sel.idx]
            print("\n=== Combinación elegida en cada semestre (top 1, coste 0,20) ===")
            print(sel[["tf", "sem", "n_ok", "train_score", "monthly", "maxDD", "trades", "all_mean", "params"]].to_string(index=False))
        for c in COSTS:
            for tf in TFS:
                x = w[(w.tf == tf) & (w.cost == c)]
                out.append(dict(top=top, cost=c, tf=tf,
                                **{f"acum_{p}": compound(x[x["sem"].str[:4].isin(ys)].monthly) for p, ys in
                                   (("21-24", ["2021", "2022", "2023", "2024"]), ("25-26", ["2025", "2026"]))},
                                sem_pos=f"{(x.monthly > 0).sum()}/{len(x)}", peor_dd=x.maxDD.min(),
                                azar_21_24=compound(x[x["sem"].str[:4].isin(["2021", "2022", "2023", "2024"])].all_mean)))
    print("\n=== Resumen fuera de muestra (rentabilidad acumulada %, semestres positivos) ===")
    res = pd.DataFrame(out); print(res.to_string(index=False)); res.to_csv(R / "wf_summary.csv", index=False)

if __name__ == "__main__":
    if "--report" not in sys.argv: simulate_all()
    report()
