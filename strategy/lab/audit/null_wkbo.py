"""Nulos para sw_wkbo H1: misma combinación elegida por semestre y mismas salidas/stop; entradas al azar. + comprar y mantener."""
import sys, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, judge as J, sw_wkbo as W
G = W.grid(); d = pd.read_csv(J.OUT / "sw_wkbo_sem.csv")
w = J.walk(d, J.RESEARCH, "H1", 0.30, 1).set_index("sem")
OOS = J.RESEARCH[2:]; old = np.array([int(s[:4]) <= 2024 for s in OOS])
rng = np.random.default_rng(11); NR = 300
K = ("rand_dir", "rand_long", "rand_long_e20")
res = {k: np.zeros((NR, len(OOS))) for k in K}; real = []; bh = []; inmk = []
for j, s in enumerate(OOS):
    df, i0 = E.load(*J.bounds(s), "H1"); p = G[int(w.loc[s, "idx"])]
    sig = W.signals(df, p); tr = []
    real.append(E.simulate(df, i0, sig, p, cost=0.30, trades=tr)["monthly"])
    hrs = sum((t["t_out"] - t["t_in"]).total_seconds() for t in tr) / 3600
    inmk.append(hrs / ((df.index[-1] - df.index[i0]).total_seconds() / 3600 * 5 / 7))
    c = df.Close.values; bh.append(c[-1] / df.Open.values[i0] - 1)
    n = len(df); idx = np.arange(i0, n - 1); nL, nS = sig["L"][i0:].sum(), sig["S"][i0:].sum()
    e20 = W.T(df, "1D", lambda x: x.Close) > W.T(df, "1D", lambda x: W.ema(x.Close, 20))
    for r in range(NR):
        for k in K:
            L = np.zeros(n, bool); S = np.zeros(n, bool)
            if k == "rand_dir":
                L[rng.choice(idx, nL, replace=False)] = True
                if nS: S[rng.choice(idx, nS, replace=False)] = True
                S &= ~L
            else:
                pool = idx if k == "rand_long" else idx[e20[idx]]
                L[rng.choice(pool, nL + nS, replace=False)] = True
            q = dict(sig); q["L"] = L; q["S"] = S
            res[k][r, j] = E.simulate(df, i0, q, p, cost=0.30)["monthly"]
real = np.array(real); ra = J.acum(real[old])
print("real acum 21-24", ra, "| 2025", J.acum(real[~old]))
print("oro por semestre %", dict(zip(OOS, np.round(np.array(bh) * 100, 1))))
print("tiempo en mercado (fracción de horas de mercado)", dict(zip(OOS, np.round(inmk, 2))))
px = {s: E.load(*J.bounds(s), "H1") for s in ("2021H2", "2024H2")}
g0 = px["2021H2"][0].Open.values[px["2021H2"][1]]; g1 = px["2024H2"][0].Close.values[-1]
print(f"oro 2021H2->2024H2: {g0:.0f} -> {g1:.0f} ({(g1/g0-1)*100:.1f} %). Comprar y mantener 1 oz sobre 1120 $: {((g1-g0)/E.CAP)*100:.1f} %")
print(f"  ajustado a la exposición media ({np.mean(np.array(inmk)[old]):.2f}): {((g1-g0)/E.CAP)*100*np.mean(np.array(inmk)[old]):.1f} %")
for k, m in res.items():
    a = np.array([J.acum(r[old]) for r in m]); b = np.array([J.acum(r[~old]) for r in m])
    print(f"{k:14s} 21-24 mediana {np.median(a):6.1f} p5 {np.percentile(a,5):6.1f} p95 {np.percentile(a,95):6.1f} P(nulo>=real) {(a >= ra).mean():.2f} | 2025 mediana {np.median(b):6.1f} P {(b >= J.acum(real[~old])).mean():.2f}")
    sem_med = np.median(m, axis=0); print("   mediana por semestre", dict(zip(OOS, np.round(sem_med, 2))))
print("real por semestre", dict(zip(OOS, real)))
