"""
Juez v3 (DIARIO/SEMANAL) del oro, 2000-2026. Criterios fijados el 2026-11-11 ANTES de probar ninguna idea.

Mide la VENTAJA con tamaño exacto (fracciones de onza, 10 000 €, riesgo 1 %): el lote mínimo de 1 oz se estudia
después (capital mínimo). Coste por lado = mayor entre 0,30 $/oz y 0,01 % del precio; estrés 0,50 $ / 0,02 %.
Swap de largos 6 %/año (el de Vantage hoy, conservador para años de tipos bajos); cortos sin swap.
v3.1 (2026-11-11, corrección de contabilidad): la posición abierta al final de cada año cuenta como operación.

Walk-forward ANUAL: cada año de 2005 a 2024 se opera con la combinación elegida SOLO con los 3 años anteriores
(media de rentabilidad anual / max(peor caída anual, 5); exige, sumando los 3 años, >= 10 operaciones y PF > 1,1).
EXAMEN reservado: 2025-01-01 -> hoy (solo el coordinador: LAB_EXAM=1 y --exam).

APROBADO v3 (coste base, top 1 salvo que se diga):
  A. Acumulado 2005-2024 > 0 con top 1 y con top 5, y > 0 en cada década (2005-14 y 2015-24) con top 1.
  B. >= 12 de 20 años no negativos y >= 60 operaciones.
  C. Caída máxima ENCADENANDO los 20 años en una sola cuenta >= -25 %.
  D. La combinación MEDIANA del grid, acumulada 2005-2024, > 0.
  E. Modelo nulo: desplazando al azar (circularmente) las señales de entrada dentro de cada año, con las mismas
     salidas, stops y combinaciones, p(nulo >= real) <= 0,10 (200 repeticiones).
  F. Mejor que comprar y mantener oro (sin swap, como un ETF): rentab. anual / |caída máx.| (MAR) >= la del oro.
  G. Con coste de estrés, el acumulado 2005-2024 top 1 sigue > 0.

Contrato de idea (strategy/lab/daily/ideas/<nombre>.py): NAME, grid() (<= 200), signals(df, p) -> dict(L, S, [XL, XS, stop]).
df = velas diarias (Open, High, Low, Close, date) con ~420 días de calentamiento. Usa dind.py para semanas/meses.
Uso: python strategy/lab/daily/djudge.py <idea> [--procs 2]
"""
import importlib, json, os, sys, time, numpy as np, pandas as pd
from multiprocessing import Pool
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE / "ideas"))
import engine as E, dind

OUT = HERE.parent.parent / "results" / "lab_daily"; OUT.mkdir(parents=True, exist_ok=True)
TRAIN0 = 2002; TEST = list(range(2005, 2025)); YEARS = list(range(TRAIN0, 2025)); EXAM = "2025"
MAX_GRID = 200; N_NULL = 200
COSTS = {"base": (0.30, 0.0001), "stress": (0.50, 0.0002)}

def rc(cost_pct): return E.RiskConfig(risk=0.01, cap_eur=10000, step=0.0001, cost_pct=cost_pct, max_risk_min_lot=1.0, mark_open=True)
def bounds(y): return (f"{y}-01-01", None) if y == EXAM else (f"{y}-01-01", f"{y}-12-31")

_D = {}; _I = None
def _init(idea, years):
    global _I
    _I = importlib.import_module(idea)
    for y in years: _D[y] = dind.load(*bounds(y))

def _job(args):
    k, p, years = args; out = []
    for y in years:
        df, i0 = _D[y]
        try: sig = _I.signals(df, p)
        except Exception as e: return [dict(idx=k, year=y, cost=c, error=repr(e)) for c in COSTS]
        for c, (usd, pct) in COSTS.items():
            out.append(dict(idx=k, year=y, cost=c, **E.simulate(df, i0, sig, p, cost=usd, rc=rc(pct))))
    return out

def run_grid(idea, years, procs):
    G = importlib.import_module(idea).grid(); assert len(G) <= MAX_GRID, f"grid() demasiado grande ({len(G)})"
    rows = []
    with Pool(procs, initializer=_init, initargs=(idea, years)) as pool:
        for rr in pool.imap_unordered(_job, [(k, p, years) for k, p in enumerate(G)], chunksize=2): rows += rr
    d = pd.DataFrame(rows)
    if "error" in d and d["error"].notna().any(): sys.exit(f"ERRORES en signals(): {d['error'].dropna().unique()[:3]}")
    return d, G

def select(d, years_test):
    """Para cada año de prueba: lista de idx ordenada por la puntuación de los 3 años anteriores (coste base)."""
    tr = d[d.cost == "base"].pivot(index="idx", columns="year")
    picks = {}
    for y in years_test:
        ys = [y - 3, y - 2, y - 1]
        gp, gl = tr["gp"][ys].sum(axis=1), tr["gl"][ys].sum(axis=1)
        ok = (tr["trades"][ys].sum(axis=1) >= 10) & (gp > 1.1 * gl) & (gp > 0)
        sc = (tr["ret"][ys].mean(axis=1) / tr["maxDD"][ys].abs().max(axis=1).clip(lower=5))[ok]
        picks[y] = list(sc.sort_values(ascending=False).index)
    return picks

def acum(r): return round((np.prod(1 + np.asarray(r, float) / 100) - 1) * 100, 1)

def chain(idea, G, picks, years, cost="base", shift_rng=None):
    """Simula el camino top 1 encadenado (una sola cuenta). shift_rng: desplaza circularmente las entradas (modelo nulo)."""
    mod = importlib.import_module(idea); usd, pct = COSTS[cost]; eq = 1.0; parts = []; ntr = 0
    for y in years:
        if not picks[y]: continue
        df, i0 = dind.load(*bounds(y)); p = G[picks[y][0]]; sig = dict(mod.signals(df, p))
        if shift_rng is not None:
            n = len(df) - i0; s = int(shift_rng.integers(1, max(n, 2)))
            for k in ("L", "S"):
                a = np.asarray(sig[k], bool).copy(); a[i0:] = np.roll(a[i0:], s); sig[k] = a
        cv = []; r = E.simulate(df, i0, sig, p, cost=usd, rc=rc(pct), curve=cv); ntr += r["trades"]
        c = pd.Series(np.asarray(cv) / cv[0] * eq if cv else [eq], index=df.index[i0:i0 + len(cv)] if cv else [df.index[i0]])
        # cv[0] ya incluye la primera vela; se reescala para empezar en eq
        c = c * (eq / c.iloc[0]) if len(c) else c
        eq = c.iloc[-1]; parts.append(c)
    cur = pd.concat(parts) if parts else pd.Series([1.0])
    return cur, ntr

def mar(cur):
    yrs = max((cur.index[-1] - cur.index[0]).days / 365.25, 0.1)
    cagr = (cur.iloc[-1] / cur.iloc[0]) ** (1 / yrs) - 1; dd = (cur / cur.cummax() - 1).min()
    return cagr * 100, dd * 100, (cagr / abs(dd) if dd < 0 else np.inf)

def buy_hold(years):
    df, i0 = dind.load(f"{years[0]}-01-01", f"{years[-1]}-12-31", warmup_days=0)
    return mar(df.Close.iloc[i0:])

def verdict(idea, d, G, years):
    picks = select(d, years)
    t = d.set_index(["cost", "year", "idx"]).sort_index()
    def path(top, cost):
        return [float(np.mean([t.loc[(cost, y, k), "ret"] for k in picks[y][:top]])) if picks[y] else 0.0 for y in years]
    r1, r5, r1s = path(1, "base"), path(5, "base"), path(1, "stress")
    trades = sum(int(t.loc[("base", y, picks[y][0]), "trades"]) if picks[y] else 0 for y in years)
    med = [float(t.loc[("base", y)].ret.median()) for y in years]
    cur, _ = chain(idea, G, picks, years)
    cagr, dd, m = mar(cur); bh_cagr, bh_dd, bh_m = buy_hold(years)
    real = cur.iloc[-1] / cur.iloc[0] - 1
    rng = np.random.default_rng(0)
    null = [chain(idea, G, picks, years, shift_rng=rng)[0] for _ in range(N_NULL)]
    null = np.array([c.iloc[-1] / c.iloc[0] - 1 for c in null]); p_null = float((null >= real).mean())
    half = [y < 2015 for y in years]
    A = acum(r1) > 0 and acum(r5) > 0 and acum([r for r, h in zip(r1, half) if h]) > 0 and acum([r for r, h in zip(r1, half) if not h]) > 0
    B = sum(r >= 0 for r in r1) >= 12 and trades >= 60
    C = dd >= -25; D_ = acum(med) > 0; E_ = p_null <= 0.10; F = m >= bh_m; G_ = acum(r1s) > 0
    res = dict(acum_top1=acum(r1), acum_top5=acum(r5), dec_05_14=acum([r for r, h in zip(r1, half) if h]),
               dec_15_24=acum([r for r, h in zip(r1, half) if not h]), anos_no_neg=f"{sum(r >= 0 for r in r1)}/{len(years)}",
               trades=trades, dd_encadenada=round(dd, 1), cagr=round(cagr, 1), MAR=round(m, 2), MAR_oro=round(bh_m, 2),
               mediana_grid=acum(med), p_nulo=round(p_null, 3), nulo_mediana=round(float(np.median(null)) * 100, 1),
               acum_estres=acum(r1s), A=A, B=B, C=C, D=D_, E=E_, F=F, G=G_, APROBADO=bool(A and B and C and D_ and E_ and F and G_))
    per_year = pd.DataFrame(dict(year=years, idx=[picks[y][0] if picks[y] else -1 for y in years], ret_top1=r1, ret_top5=r5,
                                 mediana=med, params=[json.dumps(G[picks[y][0]], ensure_ascii=False) if picks[y] else "-" for y in years]))
    return res, per_year

def main():
    idea = sys.argv[1]; procs = int(sys.argv[sys.argv.index("--procs") + 1]) if "--procs" in sys.argv else 2
    exam = "--exam" in sys.argv
    if exam and os.environ.get("LAB_EXAM") != "1": sys.exit("El examen reservado solo lo ejecuta el coordinador.")
    years = YEARS + ([EXAM] if exam else [])
    t0 = time.time(); d, G = run_grid(idea, years, procs)
    d.to_csv(OUT / f"{idea}{'_EXAM' if exam else ''}_year.csv", index=False)
    pd.set_option("display.width", 230); pd.set_option("display.max_colwidth", 110)
    if exam:
        picks = select(d.assign(year=d.year.replace(EXAM, 2025)), [2025])
        t = d[(d.year == EXAM)].set_index(["cost", "idx"])
        k = picks[2025][0]; print("EXAMEN 2025-hoy, top 1:", G[k]); print(t.loc[("base", k)]); print(t.loc[("stress", k)]); return
    res, py = verdict(idea, d, G, TEST)
    print(f"\n--- {idea} · fuera de muestra por año (coste base) ---"); print(py.to_string(index=False))
    print("\n=== VEREDICTO v3 ==="); print(pd.DataFrame([res]).T.to_string(header=False))
    reg = OUT / "registry_v3.csv"
    pd.DataFrame([dict(idea=idea, combos=len(G), fecha=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), seg=round(time.time() - t0),
                       posthoc="--posthoc" in sys.argv, **res)]).to_csv(reg, mode="a", header=not reg.exists(), index=False)

if __name__ == "__main__":
    main()
