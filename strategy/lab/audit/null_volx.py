"""Modelo nulo: misma combinación elegida por semestre, mismas salidas, entradas al azar (mismo nº de velas-señal)."""
import sys, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, judge as J, reg_volx as R
from ind import ema, atr, htf
G = R.grid(); d = pd.read_csv(J.OUT / "reg_volx_sem.csv")
w = J.walk(d, J.RESEARCH, "M30", 0.30, 1).set_index("sem")
OOS = J.RESEARCH[2:]; old = [s for s in OOS if int(s[:4]) <= 2024]
rng = np.random.default_rng(7); NR = 200
res = {k: np.zeros((NR, len(OOS))) for k in ("rand_any", "rand_regime", "rand_trend")}
real = []; expo = []; bh = []
for j, s in enumerate(OOS):
    df, i0 = E.load(*J.bounds(s), "M30"); p = G[int(w.loc[s, "idx"])]
    sig = R.signals(df, p); real.append(E.simulate(df, i0, sig, p, cost=0.30)["monthly"])
    c = df.Close
    vp = htf(df, "4h", lambda x: R._pct(atr(x, 14))); f = htf(df, "4h", lambda x: ema(x.Close, 20)); sl = htf(df, "4h", lambda x: ema(x.Close, 50)); a4 = htf(df, "4h", lambda x: atr(x, 14))
    z = (c - sl) / a4; reg = vp > p["vp"]
    bull = (reg & (f > sl) & (z > p["dist"])).values; bear = (reg & (f < sl) & (z < -p["dist"])).values
    trendL = (f > sl).values; trendS = (f < sl).values
    n = len(df); idx = np.arange(i0, n - 1)
    nL, nS = sig["L"][i0:].sum(), sig["S"][i0:].sum()
    bh.append(c.iloc[-1] / c.iloc[i0] - 1)
    for k, (poolL, poolS) in {"rand_any": (idx, idx), "rand_regime": (idx[bull[idx]], idx[bear[idx]]), "rand_trend": (idx[trendL[idx]], idx[trendS[idx]])}.items():
        for r in range(NR):
            L = np.zeros(n, bool); S = np.zeros(n, bool)
            if nL and len(poolL): L[rng.choice(poolL, min(nL, len(poolL)), replace=False)] = True
            if nS and len(poolS): S[rng.choice(poolS, min(nS, len(poolS)), replace=False)] = True
            S &= ~L
            res[k][r, j] = E.simulate(df, i0, dict(L=L, S=S, XL=sig["XL"], XS=sig["XS"]), p, cost=0.30)["monthly"]
real = np.array(real); o = np.array([s in old for s in OOS])
print("real acum 21-24", J.acum(real[o]), " 2025", J.acum(real[~o]))
print("oro buy&hold por semestre %", np.round(np.array(bh) * 100, 1))
for k, m in res.items():
    a = np.array([J.acum(r[o]) for r in m]); b = np.array([J.acum(r[~o]) for r in m])
    print(f"{k:12s} 21-24 mediana {np.median(a):6.1f} p95 {np.percentile(a,95):6.1f}  P(nulo>=real) {(a >= J.acum(real[o])).mean():.2f} | 2025 mediana {np.median(b):6.1f} P {(b >= J.acum(real[~o])).mean():.2f}")
