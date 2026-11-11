"""
Gestor de riesgo del laboratorio.

- oos_run(): repite el walk-forward de una idea (mismas combinaciones elegidas por el juez) aplicando un RiskConfig,
  encadenando los semestres fuera de muestra en una sola cuenta. Devuelve curva, operaciones (en R) y estadísticas.
- monte_carlo(): remuestrea las operaciones para estimar caídas realistas y probabilidad de perder X %.
- PRESETS: configuraciones a comparar. "adrian" = reglas de Cerebro/02-Areas/Reglas de riesgo.md.

Uso: python strategy/lab/risk.py <idea> <tf>          (necesita haber pasado antes el juez: results/lab/<idea>_sem.csv)
"""
import importlib, sys, numpy as np, pandas as pd
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "ideas"))
import engine as E, judge as J

PRESETS = {
    "base_1pct": E.RiskConfig(risk=0.01),
    "adrian_2pct": E.RiskConfig(risk=0.02, daily_loss=0.05, max_consec=4, min_rr=2.0),
    "adrian_1pct": E.RiskConfig(risk=0.01, daily_loss=0.05, max_consec=4, min_rr=2.0),
    "freno_dd_1pct": E.RiskConfig(risk=0.01, daily_loss=0.05, max_consec=4, dd_brake=0.10),
}

def oos_run(idea, tf, rc, cost=0.30, exam=False):
    sems = J.RESEARCH + (J.EXAM if exam else [])
    d = pd.read_csv(J.OUT / f"{idea}{'_EXAM' if exam else ''}_sem.csv")
    w = J.walk(d, sems, tf, cost, 1); mod = importlib.import_module(idea); G = mod.grid()
    eq = 1000.0; curve = []; trades = []; rows = []
    for _, r in w.iterrows():
        if r.idx < 0: rows.append(dict(sem=r["sem"], monthly=0.0, maxDD=0.0, trades=0)); continue
        df, i0 = E.load(*J.bounds(r["sem"]), tf); p = G[int(r.idx)]
        cv, tr = [], []
        st = E.simulate(df, i0, mod.signals(df, p), p, cost=cost, rc=rc, trades=tr, curve=cv)
        f = eq / 1000.0
        curve.append(pd.Series(np.asarray(cv) * f, index=df.index[i0:i0 + len(cv)]))
        eq = curve[-1].iloc[-1]; trades += tr
        rows.append(dict(sem=r["sem"], monthly=st["monthly"], maxDD=st["maxDD"], trades=st["trades"]))
    cur = pd.concat(curve) if curve else pd.Series([1000.0])
    dd = (cur / cur.cummax() - 1).min() * 100
    yrs = max((cur.index[-1] - cur.index[0]).days / 365.25, 0.01) if len(cur) > 1 else 1
    return dict(final=round(eq, 0), anual=round(((eq / 1000) ** (1 / yrs) - 1) * 100, 1), maxDD=round(dd, 1),
                trades=len(trades)), pd.DataFrame(rows), pd.DataFrame(trades), cur

def monte_carlo(R, risk, n_sims=5000, n_trades=None, seed=0):
    """R: resultados por operación en múltiplos del riesgo. Remuestreo con reemplazo, riesgo fijo compuesto."""
    R = np.asarray(R, float); R = R[np.isfinite(R)]
    if len(R) < 10: return {}
    rng = np.random.default_rng(seed); n_trades = n_trades or len(R)
    x = rng.choice(R, size=(n_sims, n_trades), replace=True)
    eq = np.cumprod(1 + risk * x, axis=1)
    dd = (eq / np.maximum.accumulate(eq, axis=1) - 1).min(axis=1) * 100
    fin = (eq[:, -1] - 1) * 100
    return dict(dd_mediana=round(np.median(dd), 1), dd_p95=round(np.percentile(dd, 5), 1),
                prob_dd_20=round((dd <= -20).mean() * 100, 1), prob_dd_30=round((dd <= -30).mean() * 100, 1),
                ret_mediana=round(np.median(fin), 1), prob_perder=round((fin < 0).mean() * 100, 1))

if __name__ == "__main__":
    idea, tf = sys.argv[1], sys.argv[2]
    pd.set_option("display.width", 220)
    out = []
    for name, rc in PRESETS.items():
        st, sem, tr, _ = oos_run(idea, tf, rc)
        mc = monte_carlo(tr.R.values if len(tr) else [], rc.risk)
        out.append(dict(preset=name, **st, **mc))
    print(pd.DataFrame(out).to_string(index=False))
