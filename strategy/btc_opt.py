"""
Optimización para BTCUSD en Vantage con una cuenta de 1000 € (mismo motor que el oro).

Supuestos (Vantage no publica la ficha de BTCUSD; valores conservadores):
  - 1 lote = 1 BTC, lote mínimo 0.01 BTC (paso 0.01). Si 0.01 BTC arriesga más de MAX_RISK, se descarta.
  - Coste por lado 0.02 % del precio (medio spread + deslizamiento, sin comisión). Estrés: 0.03 %.
  - Swap 20 %/año sobre el nocional en largos y cortos, cobrado cada día a las 00:00 UTC (7 días/semana).
  - Apalancamiento máximo 1:10. BTC cotiza 24/7.

Familias (máx. 3 indicadores): BO, BOH (EMA de H1/H4) y PB (RSI corto), igual que en vantage_opt.py.
Validación: selección con TRAIN = 2022-2024 y VALID = 2025 (ambos deben ser rentables);
TEST = ene-oct 2026, que no interviene en la selección.
"""
import itertools, sys, numpy as np, pandas as pd
from multiprocessing import Pool
from pathlib import Path

D = Path(__file__).parent / "data"
TFS = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h"}
EURUSD = 1.12
CAP = 1000 * EURUSD
STEP = 0.01          # BTC
MAX_RISK = 0.04
MAX_LEV = 10.0
SWAP = 0.20

PERIODS = {"train": ("2022-01-01", "2024-12-31"), "valid": ("2025-01-01", "2025-12-31"), "test": ("2026-01-01", None)}
RAW = {}

def load(period, tf):
    a, b = PERIODS[period]
    if "df" not in RAW: RAW["df"] = pd.read_pickle(D / "BTCUSDT_1m.pkl")
    df = RAW["df"]
    a2 = pd.Timestamp(a, tz="UTC") - pd.Timedelta(days=30)
    b2 = pd.Timestamp(b, tz="UTC") + pd.Timedelta(days=1) if b else None
    df = df[a2:b2]
    if tf != "M1":
        df = df.resample(TFS[tf]).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    return df, df.index.searchsorted(max(pd.Timestamp(a, tz="UTC"), df.index[0] + pd.Timedelta(days=20)))

def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def rsi(c, n):
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))
def atr(df, n=14):
    pc = df.Close.shift()
    tr = pd.concat([df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()
def htf_ema(df, rule, n):
    """EMA de una temporalidad superior, desplazada una vela (sin mirar al futuro)."""
    hc = df.Close.resample(rule).last().dropna()
    e = ema(hc, n).shift(1)
    return e.reindex(df.index, method="ffill")

def signals(df, p):
    c = df.Close
    if p["fam"] in ("BO", "BOH"):
        trend = ema(c, p["ema"]) if p["fam"] == "BO" else htf_ema(df, p["htf"], p["ema"])
        up = df.High.rolling(p["don"]).max().shift(1); dn = df.Low.rolling(p["don"]).min().shift(1)
        L = (c > up) & (c > trend); S = (c < dn) & (c < trend)
        return L.values, S.values, None, None
    trend = ema(c, p["ema"]) if p["htf"] == "same" else htf_ema(df, p["htf"], p["ema"])
    r = rsi(c, p["rsi"])
    L = (c > trend) & (r < p["lo"]); S = (c < trend) & (r > 100 - p["lo"])
    XL = (r > p["hi"]).values; XS = (r < 100 - p["hi"]).values
    return L.values, S.values, XL, XS

def simulate(df, i0, p, cost_pct=0.0002, risk=0.01):
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    risk = p.get("risk", risk)
    a = atr(df).values
    L, S, XL, XS = signals(df, p)
    hours = df.index.hour.values; day = df.index.normalize().asi8
    trailing = p["fam"] in ("BO", "BOH"); units_max = p.get("units", 1)
    s0, s1 = p.get("sess", (0, 24)); shorts = p["shorts"]; k = p["mult"]
    eq = CAP; pos = 0; units = []; stop = 0.0; pend = 0; pend_exit = False
    peak = CAP; mdd = 0.0; gp = gl = 0.0; ntr = 0; tpnl = 0.0; skipped = 0
    n = len(o)
    for i in range(i0, n):
        if pend_exit and pos:
            px = o[i] * (1 - pos * cost_pct)
            pnl = sum(pos * q * (px - e) for q, e in units); eq += pnl; tpnl += pnl
            gp += max(tpnl, 0); gl += max(-tpnl, 0); ntr += 1; tpnl = 0; pos = 0; units = []
        pend_exit = False
        if pend:
            dist = k * a[i - 1]; px = o[i]
            eqn = eq + sum(pos * q * (px - e) for q, e in units)
            q = round(np.floor(eqn * risk / dist / STEP + 1e-9) * STEP, 2)
            if q < STEP:
                q = STEP if STEP * dist <= eqn * MAX_RISK else 0
                if q == 0: skipped += 1
            expo = sum(qq for qq, _ in units) * px
            if q and (expo + q * px) <= eqn * MAX_LEV:
                if pos == 0: pos = pend; stop = px - pos * dist
                else: stop = max(stop, px - dist) if pos == 1 else min(stop, px + dist)
                units.append((q, px * (1 + pend * cost_pct)))
            pend = 0
        if pos:
            if day[i] != day[i - 1]:
                sw = sum(q for q, _ in units) * o[i] * SWAP / 365
                eq -= sw; tpnl -= sw
            hit = l[i] <= stop if pos == 1 else h[i] >= stop
            if hit:
                px = (min(o[i], stop) if pos == 1 else max(o[i], stop)) * (1 - pos * cost_pct)
                pnl = sum(pos * q * (px - e) for q, e in units); eq += pnl; tpnl += pnl
                gp += max(tpnl, 0); gl += max(-tpnl, 0); ntr += 1; tpnl = 0; pos = 0; units = []
            elif trailing:
                ns = c[i] - pos * k * a[i]
                stop = max(stop, ns) if pos == 1 else min(stop, ns)
            elif (pos == 1 and XL[i]) or (pos == -1 and XS[i]):
                pend_exit = True
        cur = eq + sum(pos * q * (c[i] - e) for q, e in units)
        peak = max(peak, cur); mdd = min(mdd, cur / peak - 1)
        if cur < CAP * 0.1: break
        if i + 1 < n and s0 <= hours[i] < s1 and not pend_exit and len(units) < units_max:
            if L[i] and pos >= 0 and (pos == 0 or trailing): pend = 1
            elif shorts and S[i] and pos <= 0 and (pos == 0 or trailing): pend = -1
    days = (df.index[-1] - df.index[i0]).days or 1
    ret = cur / CAP - 1
    return dict(trades=ntr, PF=round(gp / gl, 2) if gl else (99.0 if gp else 0.0), ret=round(ret * 100, 1),
                monthly=round(((1 + ret) ** (30.4 / days) - 1) * 100, 2) if ret > -1 else -100,
                maxDD=round(mdd * 100, 1), skipped=skipped)

def grid():
    G = []
    S = [(0, 24), (7, 20), (13, 21)]
    for ema_, don, k, u, sh, ss in itertools.product([50, 100, 200], [10, 20, 40, 60], [2.0, 3.0, 4.0, 5.0], [1, 3], [False, True], S):
        G.append(dict(fam="BO", ema=ema_, don=don, mult=k, units=u, shorts=sh, sess=ss))
    for htf, ema_, don, k, u, sh, ss in itertools.product(["1h", "4h"], [50, 200], [10, 20, 40, 60], [2.0, 3.0, 4.0], [1, 3], [False, True], S):
        G.append(dict(fam="BOH", htf=htf, ema=ema_, don=don, mult=k, units=u, shorts=sh, sess=ss))
    for htf, ema_, rn, lo, hi, k, sh, ss in itertools.product(["same", "1h"], [50, 200], [2, 3], [5, 10, 20], [60, 70, 80], [2.0, 3.0, 4.0], [False, True], S):
        G.append(dict(fam="PB", htf=htf, ema=ema_, rsi=rn, lo=lo, hi=hi, mult=k, shorts=sh, sess=ss))
    return G

DATA = {}
def init(tf):
    for per in PERIODS: DATA[per] = load(per, tf)
    RAW.clear()

def job(args):
    tf, idx, p = args
    out = dict(tf=tf, idx=idx, **{k: (f"{v[0]}-{v[1]}" if k == "sess" else v) for k, v in p.items()})
    for per in ("train", "valid"):
        df, i0 = DATA[per]
        out.update({f"{per}_{k}": v for k, v in simulate(df, i0, p).items()})
    return out

def test_job(args):
    tf, idx, p, cost = args
    df, i0 = DATA["test"]
    return dict(tf=tf, idx=idx, cost=cost, **simulate(df, i0, p, cost_pct=cost))

if __name__ == "__main__":
    tfs = sys.argv[1].split(",")
    G = grid()
    pd.set_option("display.width", 260); pd.set_option("display.max_columns", 40)
    for tf in tfs:
        with Pool(8, initializer=init, initargs=(tf,)) as pool:
            res = pd.DataFrame(pool.map(job, [(tf, i, p) for i, p in enumerate(G)], chunksize=4))
            for per in ("train", "valid"):
                res[f"{per}_score"] = res[f"{per}_monthly"] / res[f"{per}_maxDD"].abs().clip(lower=5)
            ok = res[(res.train_trades >= 40) & (res.valid_trades >= 15) & (res.train_PF > 1.1) & (res.valid_PF > 1.1)].copy()
            ok["score"] = ok[["train_score", "valid_score"]].min(axis=1)
            res.to_csv(D.parent / f"bopt_train_{tf}.csv", index=False)
            top = ok.sort_values("score", ascending=False).groupby("fam").head(3).sort_values("score", ascending=False).head(9)
            tests = pool.map(test_job, [(tf, int(r.idx), G[int(r.idx)], c) for _, r in top.iterrows() for c in (0.0002, 0.0003)]) if len(top) else []
        print(f"\n==========={tf}: {len(ok)}/{len(res)} combinaciones rentables en train y valid ===========", flush=True)
        if not tests: continue
        t = pd.DataFrame(tests).pivot_table(index="idx", columns="cost", values=["ret", "monthly", "PF", "maxDD", "trades"], aggfunc="first")
        t.columns = [f"T_{a}@{b*1e4:.0f}" for a, b in t.columns]
        show = top.set_index("idx").join(t)
        cols = [c for c in ["fam", "htf", "ema", "don", "rsi", "lo", "hi", "mult", "units", "shorts", "sess"] if c in show] + \
               ["train_trades", "train_PF", "train_monthly", "train_maxDD", "valid_trades", "valid_PF", "valid_monthly", "valid_maxDD",
                "T_trades@2", "T_PF@2", "T_monthly@2", "T_maxDD@2", "T_PF@3", "T_monthly@3"]
        print(show[cols].to_string(), flush=True)
        show.to_csv(D.parent / f"bopt_top_{tf}.csv")
