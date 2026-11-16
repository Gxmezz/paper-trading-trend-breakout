"""
BTC · segunda ronda: otros métodos (mismas condiciones Vantage que btc_opt.py, máx. 3 indicadores).

Nuevas familias:
  VB : ruptura de volatilidad intradía (Larry Williams): apertura del día UTC ± k × rango del día anterior,
       filtro EMA, stop ATR. Cierre forzado antes de la medianoche UTC (sin swap).
  BB : reversión a la media con Bandas de Bollinger + filtro EMA de H1/H4; sale en la media; stop ATR.
  BO/BOH/PB : las de la primera ronda, ahora con salidas nuevas.
Nuevas salidas (aplicables a todas): toma de beneficio a R múltiplos (tp), stop a breakeven tras 1R (be),
  cierre intradía antes de la medianoche UTC para no pagar swap (flat).
Validación igual: TRAIN 2022-24 y VALID 2025 rentables; TEST ene-oct 2026 fuera de la selección.
"""
import itertools, sys, numpy as np, pandas as pd
from multiprocessing import Pool
import btc_opt as B
from btc_opt import ema, rsi, atr, htf_ema, CAP, STEP

def signals(df, p):
    c = df.Close; f = p["fam"]
    if f in ("BO", "BOH", "PB"):
        return B.signals(df, p)
    if f == "VB":
        d = df.resample("1D").agg({"Open": "first", "High": "max", "Low": "min"})
        rng = (d.High - d.Low).shift(1).reindex(df.index, method="ffill")
        dop = d.Open.reindex(df.index, method="ffill")
        trend = htf_ema(df, p["htf"], p["ema"])
        L = (c > dop + p["k"] * rng) & (c > trend); S = (c < dop - p["k"] * rng) & (c < trend)
        return L.values, S.values, None, None
    if f == "BB":
        m = c.rolling(p["bb"]).mean(); sd = c.rolling(p["bb"]).std()
        trend = htf_ema(df, p["htf"], p["ema"])
        L = (c < m - p["dev"] * sd) & (c > trend); S = (c > m + p["dev"] * sd) & (c < trend)
        return L.values, S.values, (c > m).values, (c < m).values
    raise ValueError(f)

def simulate(df, i0, p, cost_pct=0.0002, swap=0.20, curve=False):
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    risk = p.get("risk", 0.01)
    a = atr(df).values
    L, S, XL, XS = signals(df, p)
    idx = df.index; hours = idx.hour.values; mins = idx.minute.values; day = idx.normalize().asi8
    trailing = p["fam"] in ("BO", "BOH"); units_max = p.get("units", 1)
    s0, s1 = p.get("sess", (0, 24)); shorts = p["shorts"]; k = p["mult"]
    tp = p.get("tp"); be = p.get("be", False); flat = p.get("flat", p["fam"] == "VB")
    # última vela del día (para el cierre intradía): la siguiente vela es de otro día
    last_bar = np.r_[day[1:] != day[:-1], True]
    eq = CAP; pos = 0; units = []; stop = 0.0; pend = 0; pend_exit = False; entry = 0.0; R = 0.0; tgt = None
    peak = CAP; mdd = 0.0; gp = gl = 0.0; ntr = 0; tpnl = 0.0; skipped = 0; cur = CAP
    n = len(o); eqc = np.full(n, np.nan) if curve else None

    def close_at(px):
        nonlocal eq, tpnl, gp, gl, ntr, pos, units
        pnl = sum(pos * q * (px - e) for q, e in units); eq += pnl; tpnl += pnl
        gp += max(tpnl, 0); gl += max(-tpnl, 0); ntr += 1; tpnl = 0; pos = 0; units = []

    for i in range(i0, n):
        if pend_exit and pos:
            close_at(o[i] * (1 - pos * cost_pct))
        pend_exit = False
        if pend:
            dist = k * a[i - 1]; px = o[i]
            eqn = eq + sum(pos * q * (px - e) for q, e in units)
            q = round(np.floor(eqn * risk / dist / STEP + 1e-9) * STEP, 2)
            if q < STEP:
                q = STEP if STEP * dist <= eqn * B.MAX_RISK else 0
                if q == 0: skipped += 1
            expo = sum(qq for qq, _ in units) * px
            if q and (expo + q * px) <= eqn * B.MAX_LEV:
                if pos == 0:
                    pos = pend; stop = px - pos * dist; entry = px; R = dist
                    tgt = px + pos * tp * dist if tp else None
                else: stop = max(stop, px - dist) if pos == 1 else min(stop, px + dist)
                units.append((q, px * (1 + pend * cost_pct)))
            pend = 0
        if pos:
            if day[i] != day[i - 1]:
                sw = sum(q for q, _ in units) * o[i] * swap / 365
                eq -= sw; tpnl -= sw
            hit = l[i] <= stop if pos == 1 else h[i] >= stop
            if hit:
                close_at((min(o[i], stop) if pos == 1 else max(o[i], stop)) * (1 - pos * cost_pct))
            elif tgt is not None and (h[i] >= tgt if pos == 1 else l[i] <= tgt):
                close_at((max(o[i], tgt) if pos == 1 else min(o[i], tgt)) * (1 - pos * cost_pct))
            else:
                if be and pos * (c[i] - entry) >= R:
                    stop = max(stop, entry) if pos == 1 else min(stop, entry)
                if trailing:
                    ns = c[i] - pos * k * a[i]
                    stop = max(stop, ns) if pos == 1 else min(stop, ns)
                if XL is not None and ((pos == 1 and XL[i]) or (pos == -1 and XS[i])):
                    pend_exit = True
                if flat and last_bar[i]:
                    close_at(c[i] * (1 - pos * cost_pct)); pend_exit = False
        cur = eq + sum(pos * q * (c[i] - e) for q, e in units)
        if curve: eqc[i] = cur
        peak = max(peak, cur); mdd = min(mdd, cur / peak - 1)
        if cur < CAP * 0.1: break
        if (i + 1 < n and s0 <= hours[i] < s1 and not pend_exit and len(units) < units_max
                and not (flat and last_bar[i])):
            if L[i] and pos >= 0 and (pos == 0 or trailing): pend = 1
            elif shorts and S[i] and pos <= 0 and (pos == 0 or trailing): pend = -1
    days = (df.index[-1] - df.index[i0]).days or 1
    ret = cur / CAP - 1
    r = dict(trades=ntr, PF=round(gp / gl, 2) if gl else (99.0 if gp else 0.0), ret=round(ret * 100, 1),
             monthly=round(((1 + ret) ** (30.4 / days) - 1) * 100, 2) if ret > -1 else -100,
             maxDD=round(mdd * 100, 1), skipped=skipped)
    if curve: r["curve"] = pd.Series(eqc, index=df.index).iloc[i0:].ffill()
    return r

def grid(tf):
    G = []
    S = [(0, 24), (7, 20), (13, 21)]
    X = [dict(), dict(tp=2.0), dict(tp=3.0), dict(be=True), dict(flat=True)]
    for htf, ema_, k_, m, sh, ss, rk in itertools.product(["1h", "4h"], [50, 200], [0.3, 0.5, 0.7, 1.0], [1.5, 2.0, 3.0],
                                                          [False, True], S, [dict(), dict(tp=2.0), dict(tp=3.0)]):
        G.append(dict(fam="VB", htf=htf, ema=ema_, k=k_, mult=m, shorts=sh, sess=ss, **rk))
    for htf, ema_, bb, dev, m, sh, ss, rk in itertools.product(["1h", "4h"], [50, 200], [20, 50], [2.0, 2.5, 3.0], [2.0, 3.0],
                                                               [False, True], S, [dict(), dict(flat=True)]):
        G.append(dict(fam="BB", htf=htf, ema=ema_, bb=bb, dev=dev, mult=m, shorts=sh, sess=ss, **rk))
    for htf, ema_, don, m, u, sh, ss, x in itertools.product(["4h"], [100, 200], [20, 40, 60], [3.0, 4.0, 5.0], [1, 3],
                                                             [False, True], [(0, 24), (7, 20)], X):
        G.append(dict(fam="BOH", htf=htf, ema=ema_, don=don, mult=m, units=u, shorts=sh, sess=ss, **x))
    for htf, ema_, rn, lo, hi, m, sh, ss, x in itertools.product(["same", "1h"], [50, 200], [2, 3], [5, 10], [60, 70], [2.0, 3.0],
                                                                 [False, True], [(7, 20), (13, 21)], X):
        G.append(dict(fam="PB", htf=htf, ema=ema_, rsi=rn, lo=lo, hi=hi, mult=m, shorts=sh, sess=ss, **x))
    return G

DATA = {}
def init(tf):
    for per in B.PERIODS: DATA[per] = B.load(per, tf)
    B.RAW.clear()

def job(args):
    tf, idx, p = args
    out = dict(tf=tf, idx=idx, **{k: (f"{v[0]}-{v[1]}" if k == "sess" else v) for k, v in p.items()})
    for per in ("train", "valid"):
        df, i0 = DATA[per]
        r = simulate(df, i0, p)
        out.update({f"{per}_{k}": v for k, v in r.items()})
        if per == "train" and (r["PF"] < 1.05 or r["trades"] < 40):
            break  # descartado: no hace falta simular valid
    return out

def test_job(args):
    tf, idx, p, cost = args
    df, i0 = DATA["test"]
    return dict(tf=tf, idx=idx, cost=cost, **simulate(df, i0, p, cost_pct=cost))

if __name__ == "__main__":
    tfs = sys.argv[1].split(",")
    pd.set_option("display.width", 300); pd.set_option("display.max_columns", 50)
    for tf in tfs:
        G = grid(tf)
        with Pool(8, initializer=init, initargs=(tf,)) as pool:
            res = pd.DataFrame(pool.map(job, [(tf, i, p) for i, p in enumerate(G)], chunksize=4))
            for c in ("valid_trades", "valid_PF", "valid_monthly", "valid_maxDD"):
                if c not in res: res[c] = np.nan
            for per in ("train", "valid"):
                res[f"{per}_score"] = res[f"{per}_monthly"] / res[f"{per}_maxDD"].abs().clip(lower=5)
            ok = res[(res.train_trades >= 40) & (res.valid_trades >= 15) & (res.train_PF > 1.1) & (res.valid_PF > 1.1)].copy()
            ok["score"] = ok[["train_score", "valid_score"]].min(axis=1)
            res.to_csv(B.D.parent / f"bopt2_train_{tf}.csv", index=False)
            top = ok.sort_values("score", ascending=False).groupby("fam").head(3).sort_values("score", ascending=False).head(12)
            tests = pool.map(test_job, [(tf, int(r.idx), G[int(r.idx)], c) for _, r in top.iterrows() for c in (0.0002, 0.0003)]) if len(top) else []
        print(f"\n=========== {tf}: {len(ok)}/{len(res)} rentables en train y valid ===========", flush=True)
        if not tests: continue
        t = pd.DataFrame(tests).pivot_table(index="idx", columns="cost", values=["monthly", "PF", "maxDD", "trades"], aggfunc="first")
        t.columns = [f"T_{a}@{b*1e4:.0f}" for a, b in t.columns]
        show = top.set_index("idx").join(t)
        cols = [c for c in ["fam", "htf", "ema", "k", "bb", "dev", "don", "rsi", "lo", "hi", "mult", "units", "shorts", "sess", "tp", "be", "flat"] if c in show] + \
               ["train_trades", "train_PF", "train_monthly", "train_maxDD", "valid_trades", "valid_PF", "valid_monthly", "valid_maxDD",
                "T_trades@2", "T_PF@2", "T_monthly@2", "T_maxDD@2", "T_PF@3", "T_monthly@3"]
        print(show[cols].to_string(), flush=True)
        show.to_csv(B.D.parent / f"bopt2_top_{tf}.csv")
