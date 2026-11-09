"""
Backtest intradía M1..H1: EMA (tendencia) + Donchian (ruptura) + ATR (stop/trailing).
Reglas extra que NO son indicadores: filtro de sesión (horas UTC), cierre antes del rollover,
piramidación. Costes Vantage estimados + swap si se mantiene la posición tras el rollover.

Selección honesta: se optimiza en IN-SAMPLE (2022-2024) y se mide en OUT-OF-SAMPLE (2025-2026),
datos que la optimización nunca vio.
"""
import itertools, sys, numpy as np, pandas as pd
from multiprocessing import Pool
from pathlib import Path

D = Path(__file__).parent / "data"
TF = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h"}

GOLD_COST = dict(cost_abs=0.25, cost_pct=0.0, swap_year=0.06, rollover_h=21, weekdays_only=True)
ASSETS = {
    # coste por lado oro en $/oz (medio spread 0.08 + comisión 0.03 + slippage 0.10 = 0.21; usamos 0.25)
    # Oro M1-M30: XAUUSDT perpetuo (buena calidad). IS dic-25..jun-26, OOS jul-26..oct-26
    "GOLD":  dict(file="XAUUSDT_1m.pkl", is_end="2026-06-30", oos_start="2026-07-01", **GOLD_COST),
    # 2ª validación independiente (periodo anterior): PAXG jul-24..nov-25 (todo OOS)
    "PAXG":  dict(file="PAXGUSDT_1m.pkl", is_start="2024-07-01", is_end="2025-11-30", oos_start="2025-12-01", **GOLD_COST),
    # Oro H1: futuro GC=F horario (jun-24..nov-26)
    "GOLDH1": dict(file="GCF_1h.csv", is_end="2025-09-30", oos_start="2025-10-01", **GOLD_COST),
    # BTC CFD: medio spread + slippage ~0.05% por lado; swap cripto alto
    "BTC":   dict(file="BTCUSDT_1m.pkl", is_end="2024-12-31", oos_start="2025-01-01",
                  cost_abs=0.0, cost_pct=0.0005, swap_year=0.20, rollover_h=21, weekdays_only=False),
}

def load(asset, tf, file=None):
    a = ASSETS[asset]
    f = file or a["file"]
    if f.endswith(".csv"):
        df = pd.read_csv(D / f, index_col=0); df.index = pd.to_datetime(df.index, utc=True)
        df = df[["Open", "High", "Low", "Close"]].dropna()
    else:
        df = pd.read_pickle(D / f)
    if a["weekdays_only"]:   # quitar fin de semana (el oro spot no cotiza: vie 21:00 -> dom 22:00 UTC)
        wd, h = df.index.weekday, df.index.hour
        df = df[~((wd == 5) | ((wd == 4) & (h >= 21)) | ((wd == 6) & (h < 22)))]
    if tf != "M1":
        df = df.resample(TF[tf], label="left", closed="left").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    return df

def indicators(df, ema_len, don_len, atr_len=14):
    c = df.Close
    pc = c.shift()
    tr = pd.concat([df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    return (c.ewm(span=ema_len, adjust=False).mean().values,
            df.High.rolling(don_len).max().shift(1).values,
            df.Low.rolling(don_len).min().shift(1).values,
            tr.ewm(alpha=1 / atr_len, adjust=False).mean().values)

def simulate(df, ind, asset, atr_mult, sess, shorts, flat_eod, units_max,
             risk=0.01, max_lev=10.0):
    a = ASSETS[asset]
    ema, up, dn, atr = ind
    o, h, l, c = (df[k].values.tolist() for k in ("Open", "High", "Low", "Close"))
    ema, up, dn, atr = ema.tolist(), up.tolist(), dn.tolist(), atr.tolist()
    idx = df.index
    hour = idx.hour.tolist()
    day = (idx.normalize().asi8 // 86_400_000_000_000).tolist()
    # cierre antes del rollover: última vela que empieza antes de rollover_h
    nxt_hour = hour[1:] + [hour[-1]]
    R = a["rollover_h"]; nn = len(hour)
    eod = [hour[i] < R and (i + 1 == nn or hour[i + 1] >= R or day[i + 1] != day[i]) for i in range(nn)]
    roll_cross = [(hour[i-1] < a["rollover_h"] <= hour[i]) if i else False for i in range(len(hour))]
    s0, s1 = sess
    ca, cp = a["cost_abs"], a["cost_pct"]
    swap_night = a["swap_year"] / 365
    n = len(o); eq = 1.0; pos = 0; units = []; stop = 0.0; pend = 0
    peak = 1.0; mdd = 0.0; gp = gl = 0.0; ntr = 0; trade_pnl = 0.0; cost_paid = 0.0
    eq_curve = [1.0] * n
    def cost(px): return ca + cp * px
    for i in range(n):
        if pend:
            px = o[i]; cst = cost(px); fill = px + pend * cst
            dist = atr_mult * atr[i - 1]
            if dist > 0:
                eq_now = eq + sum(pos * q * (px - e) for q, e in units)
                expo = sum(q for q, _ in units) * px
                q = min(eq_now * risk / dist, max(0.0, eq_now * max_lev - expo) / px)
                if q > 0:
                    if pos == 0: pos = pend; stop = px - pos * dist
                    else: stop = max(stop, px - dist) if pos == 1 else min(stop, px + dist)
                    units.append((q, fill)); cost_paid += q * cst
            pend = 0
        if pos:
            if roll_cross[i]:
                eq -= sum(q for q, _ in units) * o[i] * swap_night
                trade_pnl -= sum(q for q, _ in units) * o[i] * swap_night
            hit = (l[i] <= stop) if pos == 1 else (h[i] >= stop)
            exit_px = None
            if hit:
                exit_px = min(o[i], stop) if pos == 1 else max(o[i], stop)
            elif flat_eod and eod[i]:
                exit_px = c[i]
            if exit_px is not None:
                cst = cost(exit_px)
                pnl = sum(pos * q * (exit_px - pos * cst - e) for q, e in units)
                cost_paid += sum(q for q, _ in units) * cst
                eq += pnl; trade_pnl += pnl
                if trade_pnl > 0: gp += trade_pnl
                else: gl -= trade_pnl
                ntr += 1; trade_pnl = 0.0; pos = 0; units = []
            else:
                ns = c[i] - pos * atr_mult * atr[i]
                stop = max(stop, ns) if pos == 1 else min(stop, ns)
        cur = eq + sum(pos * q * (c[i] - e) for q, e in units)
        eq_curve[i] = cur
        if cur > peak: peak = cur
        dd = cur / peak - 1
        if dd < mdd: mdd = dd
        if cur <= 0.05: break
        # señales
        if i + 1 < n and s0 <= hour[i] < s1 and not (flat_eod and eod[i]) and len(units) < units_max:
            e_, u_, d_ = ema[i], up[i], dn[i]
            if u_ == u_ and c[i] > u_ and c[i] > e_ and pos >= 0: pend = 1
            elif shorts and d_ == d_ and c[i] < d_ and c[i] < e_ and pos <= 0: pend = -1
    yrs = (idx[-1] - idx[0]).days / 365.25
    final = eq_curve[-1] if eq_curve[-1] > 0 else 1e-9
    cagr = final ** (1 / yrs) - 1 if yrs > 0 and final > 0 else -1
    return dict(trades=ntr, PF=round(gp / gl, 2) if gl > 0 else 99.0, CAGR=round(cagr * 100, 1),
                maxDD=round(mdd * 100, 1), MAR=round(cagr / -mdd, 2) if mdd < 0 else 0.0,
                final=round(final, 2))

GRID = dict(ema=[50, 100, 200], don=[10, 20, 40, 60], mult=[2.0, 3.0, 4.0, 5.0],
            sess=[(0, 24), (7, 20)], shorts=[False, True], eod=[False, True], units=[1, 3])

def job(args):
    asset, tf, split, p = args
    df = CACHE[(asset, tf, split)]
    k = (asset, tf, split, p["ema"], p["don"])
    if k not in IND:
        if len(IND) > 6: IND.clear()
        IND[k] = indicators(df, p["ema"], p["don"])
    ind = IND[k]
    r = simulate(df, ind, asset, p["mult"], p["sess"], p["shorts"], p["eod"], p["units"])
    return dict(asset=asset, tf=tf, split=split, **{k: (f"{v[0]}-{v[1]}" if k == "sess" else v) for k, v in p.items()}, **r)

CACHE, IND = {}, {}
def init(asset, tfs):
    for tf in tfs:
        full = load(asset, tf)
        a = ASSETS[asset]
        for split, sub in [("IS", full[a.get("is_start"):a["is_end"]]), ("OOS", full[a["oos_start"]:a.get("oos_end")])]:
            CACHE[(asset, tf, split)] = sub

if __name__ == "__main__":
    asset = sys.argv[1]; tfs = sys.argv[2].split(",")
    MIN_TR = int(sys.argv[3]) if len(sys.argv) > 3 else 30
    keys = list(GRID); combos = [dict(zip(keys, v)) for v in itertools.product(*GRID.values())]
    tasks = [(asset, tf, "IS", p) for tf in tfs for p in combos]
    with Pool(8, initializer=init, initargs=(asset, tfs)) as pool:
        res = pool.map(job, tasks, chunksize=16)
    isr = pd.DataFrame(res)
    isr.to_csv(D.parent / f"grid_IS_{asset}_{'_'.join(tfs)}.csv", index=False)
    # Top 10 in-sample (por MAR, mínimo 30 operaciones) -> medir out-of-sample
    tasks = []
    for tf in tfs:
        top = isr[(isr.tf == tf) & (isr.trades >= MIN_TR)].sort_values("MAR", ascending=False).head(10)
        for _, r in top.iterrows():
            p = {k: r[k] for k in keys}; p["sess"] = tuple(int(x) for x in str(r["sess"]).split("-"))
            tasks.append((asset, tf, "OOS", p))
    with Pool(8, initializer=init, initargs=(asset, tfs)) as pool:
        oos = pd.DataFrame(pool.map(job, tasks))
    oos.to_csv(D.parent / f"grid_OOS_{asset}_{'_'.join(tfs)}.csv", index=False)
    pd.set_option("display.width", 250)
    for tf in tfs:
        print(f"\n===== {asset} {tf} — TOP-10 IN-SAMPLE y su OUT-OF-SAMPLE =====")
        top = isr[(isr.tf == tf) & (isr.trades >= MIN_TR)].sort_values("MAR", ascending=False).head(10)
        o = oos[oos.tf == tf]
        m = top.merge(o, on=keys + ["asset", "tf"], suffixes=("_IS", "_OOS"))
        cols = keys + ["trades_IS", "PF_IS", "CAGR_IS", "maxDD_IS", "trades_OOS", "PF_OOS", "CAGR_OOS", "maxDD_OOS", "MAR_OOS"]
        print(m[cols].to_string(index=False))
        print("Combinaciones IS con PF>1:", f"{(isr[isr.tf == tf].PF > 1).mean():.0%}")
