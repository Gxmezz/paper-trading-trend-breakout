"""Auditoría ev_macro_drift H1: todo el grid (sin elegir) con y sin fin de semana, NFP frente a FOMC, y operaciones saltadas por 1 oz mínimo."""
import sys, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, judge as J, ev_macro_drift as R
TF = "H1"; G = R.grid(); OOS = J.RESEARCH[2:]; old = np.array([int(s[:4]) <= 2024 for s in OOS])
DATA = {s: E.load(*J.bounds(s), TF) for s in OOS}
def nowk(df, s):
    ix = df.index; g = np.zeros(len(df), bool); g[:-2] = (ix[2:] - ix[:-2]) > pd.Timedelta(hours=12)
    return dict(s, XL=s["XL"] | g, XS=s["XS"] | g)
rows = []
for k, p in enumerate(G):
    for ev in ("both", "nfp", "fomc"):
        q = dict(p, ev=ev); m = {"r": [], "nw": []}; sk = []; nt = []
        for s in OOS:
            df, i0 = DATA[s]; sig = R.signals(df, q)
            st = E.simulate(df, i0, sig, q, cost=0.30); m["r"].append(st["monthly"]); sk.append(st["skipped"]); nt.append(st["trades"])
            if ev == "both": m["nw"].append(E.simulate(df, i0, nowk(df, sig), q, cost=0.30)["monthly"])
        r = np.array(m["r"]); row = dict(k=k, ev=ev, a2124=J.acum(r[old]), a25=J.acum(r[~old]), skip25=sum(sk[-2:]), n25=sum(nt[-2:]), n=sum(nt))
        if ev == "both": nw = np.array(m["nw"]); row["a2124_nowk"] = J.acum(nw[old])
        rows.append(row)
D = pd.DataFrame(rows)
for ev, x in D.groupby("ev"):
    print(ev, "acum21-24 mediana", x.a2124.median(), "% combos >0", round((x.a2124 > 0).mean() * 100), "| 2025 mediana", x.a25.median(), "| ops 2025 media", x.n25.mean().round(1), "saltadas 2025 media", x.skip25.mean().round(1))
b = D[D.ev == "both"]
print("both SIN fin de semana: acum21-24 mediana", b.a2124_nowk.median(), "% combos >0", round((b.a2124_nowk > 0).mean() * 100))
b = b.assign(**pd.DataFrame([G[i] for i in b.k]).drop(columns="ev").set_index(b.index))
for c in ("delay", "thr", "hold", "stop_atr"): print(b.groupby(c)[["a2124", "a2124_nowk"]].median().T.to_string())
