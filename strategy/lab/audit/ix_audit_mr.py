"""Auditoría escéptica de las candidatas de reversión en índices (ix_mr_dip, ix_mr_rsi2). NO toca ficheros existentes.

Variantes por entorno (se aplican también en los procesos hijos):
  IXA_ENTRY=open|close   close = entrada al CIERRE del día siguiente a la señal (no a su apertura)
  IXA_DATA=yahoo|cfd     cfd = precios del CFD de Vantage (MT5, desde 2018-03)
  IXA_COST=0.0001        coste por lado (fracción); el de estrés es el doble
  IXA_SWAP=1|0           0 = sin swap
Uso: python ix_audit_mr.py <idea> --asset NDX|SPX <accion> [años]
  wf   y0 y1   -> grid completo + walk-forward; resumen en el periodo completo y en sub-periodos (aperturas reales)
  fixed y0 y1  -> usa las elecciones del CSV oficial del juez (Yahoo, coste base) y las encadena con los datos actuales
  cuts         -> prueba de cortes (sin mirar al futuro)
Nunca usa fechas posteriores a 2024-12-31.
"""
import importlib, inspect, os, sys, json, pickle, numpy as np, pandas as pd
from pathlib import Path
HERE = Path(__file__).resolve().parent
DAILY = HERE.parent / "daily"
for p in (DAILY / "ideas", HERE.parent, DAILY): sys.path.insert(0, str(p))
ENTRY = os.environ.get("IXA_ENTRY", "open"); DATA = os.environ.get("IXA_DATA", "yahoo")
COST = float(os.environ.get("IXA_COST", "0.0001")); SWAP = os.environ.get("IXA_SWAP", "1") == "1"
import djudge as J, dind, engine as E

J.COSTS = {"base": (0.0, COST), "stress": (0.0, 2 * COST)}
if DATA == "cfd": dind.CFG["file"] = {"NDX": "NAS100_mt5_D1.pkl", "SPX": "SP500_mt5_D1.pkl"}[dind.ASSET]
if not SWAP:
    _raw = dind.raw
    def _raw_noswap():
        r = _raw()
        if "noswap" not in r.attrs: r["swl"] = 0.0; r["sws"] = 0.0; r.attrs["noswap"] = 1
        return r
    dind.raw = _raw_noswap
if ENTRY == "close":
    src = inspect.getsource(E.simulate)
    a, b = "dist = sd[i - 1]; px = o[i]", "if pos:\n            hit_s"
    assert a in src and b in src
    exec(src.replace(a, "dist = sd[i - 1]; px = c[i]").replace(b, "if pos and t_in != i:\n            hit_s"), E.__dict__)

CACHE = Path(os.environ.get("IXA_CACHE", HERE / "_ixa_cache")); CACHE.mkdir(exist_ok=True)
TAG = f"{dind.ASSET}_{ENTRY}_{DATA}_{COST}_{int(SWAP)}"

def chain_tr(idea, G, picks, years):
    """Camino top 1 encadenado + lista de operaciones (r = pnl / capital al inicio del año)."""
    mod = importlib.import_module(idea); eq = 1.0; parts = []; trs = []
    for y in years:
        if not picks.get(y): continue
        df, i0 = dind.load(*J.bounds(y)); p = G[picks[y][0]]
        cv = []; tl = []; E.simulate(df, i0, mod.signals(df, p), p, cost=0.0, rc=J.rc(COST), curve=cv, trades=tl)
        c = pd.Series(np.asarray(cv) / cv[0] * eq, index=df.index[i0:i0 + len(cv)]); eq = c.iloc[-1]; parts.append(c)
        for t in tl: trs.append(dict(year=y, t_in=t["t_in"], r=t["pnl"] / 10000, R=t["R"]))
    return pd.concat(parts), pd.DataFrame(trs)

def null_p(idea, G, picks, years, real, n=200):
    rng = np.random.default_rng(0)
    nl = np.array([(lambda c: c.iloc[-1] / c.iloc[0] - 1)(J.chain(idea, G, picks, years, shift_rng=rng)[0]) for _ in range(n)])
    return float((nl >= real).mean()), float(np.median(nl) * 100), float((real - nl.mean()) / nl.std())

def summary(idea, d, G, years, picks, label, n_null=200):
    t = d.set_index(["cost", "year", "idx"]).sort_index()
    r1 = [float(t.loc[("base", y, picks[y][0]), "ret"]) if picks[y] else 0.0 for y in years]
    r5 = [float(np.mean([t.loc[("base", y, k), "ret"] for k in picks[y][:5]])) if picks[y] else 0.0 for y in years]
    med = [float(t.loc[("base", y)].ret.median()) for y in years]
    cur, tr = chain_tr(idea, G, {y: picks[y] for y in years}, years)
    cagr, dd, m = J.mar(cur); _, bdd, bm = J.buy_hold(years)
    real = cur.iloc[-1] / cur.iloc[0] - 1
    p, nmed, z = null_p(idea, G, picks, years, real, n_null) if n_null else (np.nan, np.nan, np.nan)
    return dict(tramo=label, top1=J.acum(r1), top5=J.acum(r5), med_grid=J.acum(med), anos_pos=f"{sum(r >= 0 for r in r1)}/{len(years)}",
                trades=len(tr), dd=round(dd, 1), cagr=round(cagr, 2), MAR=round(m, 2), MAR_bh=round(bm, 2),
                p_nulo=round(p, 3), nulo_med=round(nmed, 1), z_nulo=round(z, 1)), tr

def drop_best(tr, years, k):
    """Acumulado si se quitan las k mejores operaciones (por r)."""
    keep = tr.drop(tr.r.nlargest(k).index) if k else tr
    by = keep.groupby("year").r.sum().reindex(years, fill_value=0)
    return round((np.prod(1 + by.values) - 1) * 100, 1)

def main():
    idea = sys.argv[1]; act = [a for a in sys.argv[2:] if a in ("wf", "fixed", "cuts")][0]
    nums = [int(a) for a in sys.argv[2:] if a.isdigit()]
    pd.set_option("display.width", 250)
    print(f"### {idea} {dind.ASSET} entrada={ENTRY} datos={DATA} coste={COST} swap={SWAP}")
    if act == "cuts":
        mod = importlib.import_module(idea); G = mod.grid(); df, i0 = dind.load("1995-01-01", "2024-12-31"); bad = 0
        rng = np.random.default_rng(1)
        for k in rng.integers(i0 + 50, len(df), 25):
            for p in [G[j] for j in rng.integers(0, len(G), 8)]:
                full = mod.signals(df, p); cut = mod.signals(df.iloc[:k + 1], p)
                for key in cut:
                    if np.asarray(cut[key])[k] != np.asarray(full[key])[k]: bad += 1
        print("diferencias cortado vs completo:", bad); return
    if act == "fixed":
        y0, y1 = nums; years = list(range(y0, y1 + 1))
        off = pd.read_csv(J.OUT.parent / dind.ASSET / f"{idea}_year.csv"); G = importlib.import_module(idea).grid()
        off["cost"] = off.cost.astype(str); picks = J.select(off, years)
        cur, tr = chain_tr(idea, G, picks, years); cagr, dd, m = J.mar(cur); _, _, bm = J.buy_hold(years)
        by = tr.groupby("year").r.sum().reindex(years, fill_value=0) * 100
        print("por año (%):", by.round(1).to_dict())
        print(dict(acum=round((cur.iloc[-1] / cur.iloc[0] - 1) * 100, 1), trades=len(tr), dd=round(dd, 1), MAR=round(m, 2), MAR_bh=round(bm, 2),
                   params=[json.dumps(G[picks[y][0]]) for y in years]))
        return
    y0, y1 = nums if nums else (J.TRAIN0, 2024); years = list(range(y0, y1 + 1))
    f = CACHE / f"{idea}_{TAG}_{y0}_{y1}.pkl"
    if f.exists(): d, G = pickle.loads(f.read_bytes())
    else:
        d, G = J.run_grid(idea, years, int(os.environ.get("IXA_PROCS", "2"))); f.write_bytes(pickle.dumps((d, G)))
    tests = [y for y in years if y >= y0 + 3]; picks = J.select(d, tests)
    subs = [(f"{tests[0]}-2024", tests)]
    for a, b in [(1990, 2000), (2001, 2012), (2013, 2024), (2004, 2024), (2008, 2024), (2017, 2024)]:
        ys = [y for y in tests if a <= y <= b]
        if ys and ys != tests: subs.append((f"{a}-{b}", ys))
    n_null = int(os.environ.get("IXA_NULL", "200"))
    rows = []; trs = None
    for lab, ys in subs:
        r, tr = summary(idea, d, G, ys, picks, lab, n_null if lab == subs[0][0] or os.environ.get("IXA_NULLSUB") else 0)
        rows.append(r); trs = tr if trs is None else trs
    print(pd.DataFrame(rows).to_string(index=False))
    ys = subs[0][1]
    print("quitar k mejores operaciones (acum %):", {k: drop_best(trs, ys, k) for k in (0, 5, 10, 20)},
          f"| R medio {trs.R.mean():.3f}, mediana {trs.R.median():.3f}, % ganadoras {(trs.r > 0).mean():.2f}")
    dec = trs.assign(dec=(trs.year // 10) * 10).groupby("dec").agg(n=("r", "size"), suma_pct=("r", lambda s: round(s.sum() * 100, 1)),
                                                                     R_medio=("R", "mean"))
    print(dec.round(3).to_string())
    # grid completo sin elegir: acumulado de cada combinación en los años de prueba
    b = d[d.cost == "base"].pivot(index="idx", columns="year", values="ret")[ys]
    ac = ((1 + b / 100).prod(axis=1) - 1) * 100
    print(f"grid sin elegir ({len(ac)} comb.): mediana {ac.median():.1f} %, p10 {ac.quantile(.1):.1f}, p90 {ac.quantile(.9):.1f}, "
          f"% > 0: {(ac > 0).mean():.2f}")
    pk = pd.Series([json.dumps({k: v for k, v in G[picks[y][0]].items()}) for y in ys if picks[y]]).value_counts().head(5)
    print("combinaciones más elegidas:"); print(pk.to_string())

if __name__ == "__main__":
    main()
