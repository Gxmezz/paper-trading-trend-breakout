"""Examen prerregistrado (ver PREREGISTRO.md).

  python strategy/lab/copy_hl/exam.py design   -> elige parámetros con 2025-03..2025-12 y los congela
  python strategy/lab/copy_hl/exam.py exam     -> aplica los congelados a 2026-01-01..2026-11-15 (una vez)
"""
import json, pickle, sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from build import COST, DATA, H0, MS_H, NH, NW  # noqa: E402

HERE = Path(__file__).parent
FROZEN = HERE / "parametros_congelados.json"
K = 10
D = 24
W = 168


def hour(s):
    return int((pd.Timestamp(s, tz="UTC").value // 1_000_000 - H0) // MS_H)


DESIGN = (hour("2025-03-01"), hour("2026-01-01"))
EXAM = (hour("2026-01-01"), NH - 1)


# ------------------------------------------------------------------ datos
class Data:
    def __init__(self):
        s = pickle.load(open(DATA / "series.pkl", "rb"))
        ws = [w for w in s["wallets"] if w["h0"] is not None]
        self.ws, self.btc = ws, s["btc"]
        n = len(ws)
        self.P = np.full((n, NH), np.nan, np.float32)
        self.G = np.zeros((n, NH), np.float32)
        self.h0 = np.array([w["h0"] for w in ws])
        for i, w in enumerate(ws):
            self.P[i, w["h0"]:] = w["P"]; self.G[i, w["h0"]:] = w["G"]
        z = lambda k: np.cumsum(np.vstack([np.r_[0, w[k]] for w in ws]), axis=1)
        self.c_ord, self.c_notl, self.c_wl = z("n_ord"), z("notl"), z("wl")
        self.n_ord = np.vstack([w["n_ord"] for w in ws])
        self.lev = np.vstack([w["lev"] for w in ws])
        self.lev[self.lev <= 0] = np.nan  # sin valor de cuenta conocido: dato ausente, no 0
        hs = np.vstack([w["hold_sum"] for w in ws]); hn = np.vstack([w["hold_n"] for w in ws])
        with np.errstate(invalid="ignore", divide="ignore"):
            self.hold = np.where(hn > 0, hs / hn, np.nan)
        self.cache = {}
        print(f"carteras con copia: {n}", flush=True)

    def week_sum(self, c, i, w0, w1):
        w0, w1 = max(w0, 0), min(w1, NW)
        return c[i, w1] - c[i, w0]

    # ---- filtro de copiable y métricas para elegir, con datos < t
    def scores(self, t):
        if t in self.cache:
            return self.cache[t]
        wT = t // W
        idx = np.where(self.h0 <= t - 90 * D)[0]
        rows = []
        for i in idx:
            if self.week_sum(self.c_ord, i, wT - 4, wT) <= 0:
                continue
            if self.week_sum(self.c_ord, i, wT - 13, wT) / 91 > 40:
                continue
            notl = self.week_sum(self.c_notl, i, wT - 26, wT)
            if notl <= 0 or self.week_sum(self.c_wl, i, wT - 26, wT) / notl < 0.7:
                continue
            h = self.ws[i]["holds"]
            h = h[(h[:, 0] >= H0 + (t - 180 * D) * MS_H) & (h[:, 0] < H0 + t * MS_H)] if len(h) else h
            if len(h) == 0 or np.median(h[:, 1]) < 30:
                continue
            a = max(t - 180 * D, self.h0[i])
            p = self.P[i, a:t + 1].astype(np.float64)
            dd = float(np.max(np.maximum.accumulate(p) - p))
            wk = p[::W]
            r = np.diff(np.r_[wk, p[-1]]) if len(wk) else np.array([0.0])
            sh = r.mean() / r.std() * np.sqrt(52) if len(r) > 2 and r.std() > 0 else 0.0
            p90 = self.P[i, max(t - 90 * D, self.h0[i])]
            rows.append((i, float(p[-1] - p90), sh, float((p[-1] - p[0]) / max(dd, 0.01)), dd))
        df = pd.DataFrame(rows, columns=["i", "ret90", "sharpe", "retdd", "dd"]).set_index("i")
        self.cache[t] = df
        return df

    # ---- detector de cambio de estilo (semana anterior a t frente a sus 26 previas)
    def drift(self, i, t, f):
        w = t // W - 1
        if w < 9:
            return False
        hist = slice(max(w - 26, 0), w)
        for arr, active in ((self.n_ord, self.n_ord[i, hist] > 0), (self.lev, ~np.isnan(self.lev[i, hist])),
                            (self.hold, ~np.isnan(self.hold[i, hist]))):
            x = arr[i, hist][active]
            v = arr[i, w]
            if len(x) < 8 or not np.isfinite(v) or self.n_ord[i, w] == 0:
                continue
            lo, hi = np.percentile(x, 10), np.percentile(x, 90)
            if v < lo / f or v > hi * f:
                return True
        tc = self.ws[i]["topc"]
        if tc and self.n_ord[i, w] > 0:
            last = max(tc, key=lambda c: tc[c][w])
            tot = {c: float(a[hist].sum()) for c, a in tc.items()}
            top3 = sorted(tot, key=tot.get, reverse=True)[:3]
            if tc[last][w] > 0 and last not in top3:
                return True
        return False


def ranked(d, t, kind, w=0.5):
    s = d.scores(t)
    if s.empty:
        return []
    if kind == "S1":
        return list(s.sort_values("ret90", ascending=False).index)
    s = s[s.dd <= 0.40]
    sc = w * s.sharpe.rank(pct=True) + (1 - w) * s.retdd.rank(pct=True)
    return list(sc.sort_values(ascending=False).index)


def month_starts(hs, he):
    t = pd.Timestamp(H0 * 1_000_000, tz="UTC") + pd.Timedelta(hours=hs)
    out = [hs]
    m = (t + pd.offsets.MonthBegin(1)).normalize()
    while True:
        h = int((m.value // 1_000_000 - H0) // MS_H)
        if h >= he:
            return out
        out.append(h); m += pd.offsets.MonthBegin(1)


# ------------------------------------------------------------------ cartera
def run(d, kind, hs, he, X=None, f=None, w=0.5, rng=None):
    months = set(month_starts(hs, he))
    mondays = set(range((hs // W + 1) * W, he, W)) if kind == "S3" else set()
    cps = sorted(months | mondays | {he})
    eq = np.full(he - hs + 1, np.nan); eq[0] = 1.0
    sl, cash, nrep = [], 1.0, 0  # sleeve: dict(i, e, g=índice desde la entrada, pk)
    for a, b in zip(cps[:-1], cps[1:]):
        if a in months:
            E = cash + sum(s["e"] for s in sl)
            if kind == "S0":
                pool = list(d.scores(a).index)
                new = list(rng.choice(pool, size=min(K, len(pool)), replace=False)) if pool else []
            else:
                new = ranked(d, a, kind, w)[:K]
            keep = {s["i"]: s for s in sl}
            for s in sl:
                if s["i"] not in new:
                    E -= COST * float(d.G[s["i"], a]) * s["e"]
            sl2 = []
            for i in new:
                e = E / K
                if i in keep:
                    s = keep[i]; s["e"] = e
                else:
                    s = dict(i=i, e=e * (1 - COST * float(d.G[i, a])), g=1.0, pk=1.0)
                sl2.append(s)
            sl, cash = sl2, E - (E / K) * len(new)
        elif kind == "S3":
            cur = {s["i"] for s in sl}
            for s in sl:
                if (1 - s["g"] / s["pk"]) > X or d.drift(s["i"], a, f):
                    cand = [j for j in ranked(d, a, "S2", w) if j not in cur]
                    if not cand:
                        continue
                    j = cand[0]; nrep += 1
                    e = s["e"] * (1 - COST * float(d.G[s["i"], a])) * (1 - COST * float(d.G[j, a]))
                    cur.discard(s["i"]); cur.add(j)
                    s.update(i=j, e=e, g=1.0, pk=1.0)
        tot = np.full(b - a, cash)
        for s in sl:
            p = d.P[s["i"], a + 1:b + 1].astype(np.float64) - float(d.P[s["i"], a])
            v = s["e"] * (1 + p)
            dead = np.maximum.accumulate(v <= 0)
            v[dead] = 0.0
            g = s["g"] * (1 + p); g[dead] = 0.0
            s["pk"] = max(s["pk"], float(g.max()) if len(g) else s["pk"])
            s["g"] = float(g[-1]); s["e"] = float(v[-1])
            tot += v
        eq[a + 1 - hs:b + 1 - hs] = tot
    return eq, nrep


def stats(eq):
    eq = pd.Series(eq).ffill().values
    ret = eq[-1] / eq[0] - 1
    yrs = (len(eq) - 1) / (24 * 365)
    ann = (eq[-1] / eq[0]) ** (1 / yrs) - 1 if eq[-1] > 0 else -1
    dd = float(np.max(1 - eq / np.maximum.accumulate(eq)))
    return dict(ret=ret, mes=(1 + ann) ** (1 / 12) - 1, dd=dd, mar=ann / dd if dd > 0 else np.nan)


def btc_stats(d, hs, he):
    return stats(d.btc[hs:he + 1].astype(np.float64))


def fmt(s):
    return f"{s['ret']*100:+7.1f} %  ({s['mes']*100:+5.2f} %/mes)  caída {s['dd']*100:5.1f} %  MAR {s['mar']:5.2f}"


# ------------------------------------------------------------------ fases
def design():
    d = Data(); hs, he = DESIGN
    res = []
    for w in (0.0, 0.5, 1.0):
        s = stats(run(d, "S2", hs, he, w=w)[0]); res.append(("S2", w, None, None, s))
        print("S2", w, fmt(s), flush=True)
        for X in (0.15, 0.25, 0.35):
            for f in (1.0, 1.5, 2.0):
                eq, nrep = run(d, "S3", hs, he, X=X, f=f, w=w)
                s = stats(eq); s["nrep"] = nrep; res.append(("S3", w, X, f, s))
                print("S3", w, X, f, fmt(s), "sustituciones", nrep, flush=True)
    s1 = stats(run(d, "S1", hs, he)[0]); print("S1", fmt(s1))
    best2 = max((r for r in res if r[0] == "S2"), key=lambda r: r[4]["mar"])
    best3 = max((r for r in res if r[0] == "S3"), key=lambda r: r[4]["mar"])
    fr = {"S2": {"w": best2[1]}, "S3": {"w": best3[1], "X": best3[2], "f": best3[3]},
          "diseño": {"S1": s1, "S2": best2[4], "S3": best3[4], "BTC": btc_stats(d, hs, he)}}
    FROZEN.write_text(json.dumps(fr, indent=1, default=float))
    pd.DataFrame([dict(kind=r[0], w=r[1], X=r[2], f=r[3], **r[4]) for r in res]).to_csv(HERE / "diseno_rejilla.csv", index=False)
    print("congelados:", fr["S2"], fr["S3"])


def exam():
    fr = json.loads(FROZEN.read_text())
    out = HERE / "resultado_examen.json"
    if out.exists():
        sys.exit("El examen ya se hizo: resultado_examen.json existe.")
    d = Data(); hs, he = EXAM
    r = {}
    r["S1"] = stats(run(d, "S1", hs, he)[0])
    r["S2"] = stats(run(d, "S2", hs, he, w=fr["S2"]["w"])[0])
    eq3, nrep = run(d, "S3", hs, he, **fr["S3"])
    r["S3"] = stats(eq3); r["S3"]["nrep"] = nrep
    r["BTC"] = btc_stats(d, hs, he)
    rng = np.random.default_rng(42)
    s0 = [stats(run(d, "S0", hs, he, rng=rng)[0]) for _ in range(500)]
    r0 = np.array([s["ret"] for s in s0])
    r["S0"] = {"ret_mediana": float(np.median(r0)), "ret_p90": float(np.percentile(r0, 90)),
               "dd_mediana": float(np.median([s["dd"] for s in s0]))}
    for k in ("S2", "S3"):
        s = r[k]
        s["pct_azar"] = float((r0 < s["ret"]).mean())
        s["A"] = s["ret"] > 0
        s["B"] = s["pct_azar"] >= 0.90
        s["D"] = s["dd"] <= 0.30 and s["mar"] > r["BTC"]["mar"]
    r["S3"]["C"] = r["S3"]["ret"] > max(r["S1"]["ret"], r["S2"]["ret"])
    r["S3"]["aprueba"] = all(r["S3"][k] for k in "ABCD")
    r["S2"]["aprueba"] = all(r["S2"][k] for k in "ABD")
    out.write_text(json.dumps(r, indent=1, default=lambda o: bool(o) if isinstance(o, np.bool_) else float(o)))
    np.save(HERE.parent.parent / "results" / "copy_hl_eq_S3.npy", eq3) if (HERE.parent.parent / "results").exists() else None
    for k in ("S1", "S2", "S3", "BTC"):
        print(k, fmt(r[k]), {x: r[k][x] for x in ("pct_azar", "A", "B", "C", "D", "aprueba") if x in r[k]})
    print("S0 azar: mediana", f"{r['S0']['ret_mediana']*100:+.1f} %", "p90", f"{r['S0']['ret_p90']*100:+.1f} %")


if __name__ == "__main__":
    {"design": design, "exam": exam}[sys.argv[1]]()
