"""
Juez común: walk-forward con XAUUSD real. Todas las ideas se miden con las mismas reglas, fijadas ANTES de probar ninguna.

  1. Cada combinación de grid() se simula en cada semestre por separado (riesgo 1 %, sin reglas diarias: se mide la ventaja).
  2. Para operar el semestre S se elige con los 2 semestres anteriores: peor de los dos (rent. mensual / max(caída, 5)),
     exigiendo, SUMANDO los dos semestres, >= 12 operaciones y PF > 1,1 (v2: así puede elegirse una idea que estuvo quieta).
  3. Fuera de muestra para investigar: 2021H2-2025H2 (9 semestres). 2026H1-2026H2 = EXAMEN RESERVADO (solo el coordinador).

APROBADO v2 (fijado el 2026-11-11 antes de la ronda 2; coste 0,30 $/oz, todo a la vez):
  A. Acumulado 2021H2-2024H2 > 0 con la mejor combinación (top 1) Y con la media de las 5 mejores (top 5).
  B. >= 6 de 9 semestres NO negativos (top 1; un semestre sin operar cuenta como 0) y >= 40 operaciones en total.
  C. Peor caída de un semestre >= -20 % (top 1).
  D. La familia funciona sin elegir: la combinación MEDIANA del grid, acumulada 2021H2-2024H2 (años laterales), > 0.
(v1, ronda 1: ventanas de selección por separado, B = 6/9 positivos, D = top 1 gana a la media del grid en 6/9.)

Uso: python strategy/lab/judge.py <idea> [--procs 3]        (idea = fichero en strategy/lab/ideas sin .py)
Cada ejecución queda registrada en strategy/results/lab/registry_v2.csv (v1: registry.csv) (cuántas ideas se han probado importa).
"""
import importlib, json, os, sys, time, numpy as np, pandas as pd
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "ideas"))
import engine as E

OUT = HERE.parent / "results" / "lab"; OUT.mkdir(parents=True, exist_ok=True)
RESEARCH = ["2020H2", "2021H1", "2021H2", "2022H1", "2022H2", "2023H1", "2023H2", "2024H1", "2024H2", "2025H1", "2025H2"]
EXAM = ["2026H1", "2026H2"]
COSTS = (0.20, 0.30)
MAX_GRID = 300

def bounds(s):
    y, h = int(s[:4]), s[-1]
    return (f"{y}-01-01", f"{y}-06-30") if h == "1" else (f"{y}-07-01", None if y == 2026 else f"{y}-12-31")

_DATA = {}; _IDEA = None
def _init(idea, tf, sems):
    global _IDEA
    _IDEA = importlib.import_module(idea)
    for s in sems: _DATA[s] = E.load(*bounds(s), tf)

def _job(args):
    tf, k, p, sems = args
    out = []
    for s in sems:
        df, i0 = _DATA[s]
        try: sig = _IDEA.signals(df, p)
        except Exception as e: return [dict(tf=tf, idx=k, sem=s, cost=c, error=repr(e)) for c in COSTS]
        for c in COSTS:
            out.append(dict(tf=tf, idx=k, sem=s, cost=c, **E.simulate(df, i0, sig, p, cost=c)))
    return out

def run_grid(idea, sems, procs=3):
    mod = importlib.import_module(idea); G = mod.grid()
    assert len(G) * len(mod.TFS) <= MAX_GRID * 2 and len(G) <= MAX_GRID, f"grid() demasiado grande ({len(G)} > {MAX_GRID})"
    assert set(mod.TFS) <= {"M30", "H1"}, "TFS solo puede contener M30 y/o H1"
    rows = []
    for tf in mod.TFS:
        with Pool(procs, initializer=_init, initargs=(idea, tf, sems)) as pool:
            for rr in pool.imap_unordered(_job, [(tf, k, p, sems) for k, p in enumerate(G)], chunksize=4): rows += rr
    d = pd.DataFrame(rows)
    if "error" in d and d["error"].notna().any():
        print("ERRORES en signals():", d["error"].dropna().unique()[:3]); sys.exit(1)
    return d, G

def score(m, dd): return m / dd.abs().clip(lower=5)

def walk(d, sems, tf, cost, top):
    x = d[d.tf == tf]
    tr = x[x.cost == 0.20].pivot(index="idx", columns="sem")       # la selección siempre con coste 0,20
    rows = []
    for j in range(2, len(sems)):
        a, b, s = sems[j - 2], sems[j - 1], sems[j]
        gp, gl = tr["gp"][[a, b]].sum(axis=1), tr["gl"][[a, b]].sum(axis=1)
        ok = (tr["trades"][[a, b]].sum(axis=1) >= 12) & (gp > 1.1 * gl) & (gp > 0)
        sc = pd.concat([score(tr["monthly"][z], tr["maxDD"][z]) for z in (a, b)], axis=1).min(axis=1)[ok].sort_values(ascending=False)
        te = x[(x["sem"] == s) & (x.cost == cost)].set_index("idx")
        if len(sc): pk = sc.index[:top]; m, dd, nt, idx = te.loc[pk, "monthly"].mean(), te.loc[pk, "maxDD"].mean(), te.loc[pk, "trades"].mean(), int(pk[0])
        else: m, dd, nt, idx = 0.0, 0.0, 0, -1                      # nada pasa el filtro: no se opera
        rows.append(dict(sem=s, idx=idx, n_ok=len(sc), monthly=round(m, 2), maxDD=round(dd, 1), trades=int(nt), azar=round(te.monthly.mean(), 2), mediana=round(te.monthly.median(), 2)))
    return pd.DataFrame(rows)

def acum(m): return round((np.prod(1 + np.asarray(m, float) / 100) ** 6 - 1) * 100, 1)

def verdict(d, sems, tf):
    w1 = walk(d, sems, tf, 0.30, 1); w5 = walk(d, sems, tf, 0.30, 5)
    old = w1["sem"].str[:4].astype(int) <= 2024
    A = acum(w1[old].monthly) > 0 and acum(w5[old].monthly) > 0
    B = (w1.monthly >= 0).sum() >= 6 and w1.trades.sum() >= 40
    C = w1.maxDD.min() >= -20
    D = acum(w1[old].mediana) > 0
    res = dict(tf=tf, acum_21_24_top1=acum(w1[old].monthly), acum_21_24_top5=acum(w5[old].monthly),
               acum_25_top1=acum(w1[~old].monthly), sem_no_neg=f"{(w1.monthly >= 0).sum()}/{len(w1)}", trades=int(w1.trades.sum()),
               peor_dd=w1.maxDD.min(), mediana_21_24=acum(w1[old].mediana), gana_al_azar=f"{(w1.monthly > w1.azar).sum()}/{len(w1)}",
               A=A, B=B, C=C, D=D, APROBADO=bool(A and B and C and D))
    return res, w1

def main():
    idea = sys.argv[1]; procs = int(sys.argv[sys.argv.index("--procs") + 1]) if "--procs" in sys.argv else 3
    exam = "--exam" in sys.argv
    if exam and os.environ.get("LAB_EXAM") != "1": sys.exit("El examen reservado solo lo ejecuta el coordinador.")
    sems = RESEARCH + (EXAM if exam else [])
    t0 = time.time(); d, G = run_grid(idea, sems, procs)
    tag = "_EXAM" if exam else ""
    d.to_csv(OUT / f"{idea}{tag}_sem.csv", index=False)
    pd.set_option("display.width", 220); pd.set_option("display.max_colwidth", 120)
    verdicts = []
    for tf in importlib.import_module(idea).TFS:
        v, w1 = verdict(d, sems, tf); verdicts.append(v)
        w1["params"] = [json.dumps(G[i], ensure_ascii=False) if i >= 0 else "-" for i in w1.idx]
        print(f"\n--- {idea} · {tf} · fuera de muestra, top 1, coste 0,30 ---")
        print(w1.to_string(index=False))
    vv = pd.DataFrame(verdicts); print("\n=== VEREDICTO ==="); print(vv.to_string(index=False))
    reg = OUT / "registry_v2.csv"
    rr = vv.assign(idea=idea, posthoc="--posthoc" in sys.argv, combos=len(G), exam=exam, fecha=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), seg=round(time.time() - t0))
    rr.to_csv(reg, mode="a", header=not reg.exists(), index=False)

if __name__ == "__main__":
    main()
