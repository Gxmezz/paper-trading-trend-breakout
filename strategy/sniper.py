"""
Backtest del indicador "QNA SNIPER MATRIX" (TradingView) sin repintado, y prueba como filtro de la estrategia actual.

Reglas del indicador:
  - Compra: cruce al alza SMA 9 / SMA 21 en la temporalidad del gráfico, con SMA 9 > SMA 21 en H1.
  - Venta: cruce a la baja, con SMA 9 < SMA 21 en H1.
  - Stop: mínimo (compras) / máximo (ventas) de las últimas 10 velas. TP1/TP2/TP3 a 1R, 2R y 3R.
  - Una señal contraria cierra la operación y abre la contraria.
Diferencia con el original: en TradingView usa lookahead_on, que en el histórico mira el cierre FUTURO de la
vela de H1. Aquí la H1 se usa solo cuando ya ha cerrado.

Gestión simulada: 3 parciales iguales en 1R/2R/3R, sin break even (como el indicador), riesgo 1 %, costes 0,20 $/oz.
Uso: python sniper.py
"""
import sys, numpy as np, pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import vantage_opt as vo, combine as cb
from paper_trader import ACCOUNTS

vo.PERIODS["XAU_test"] = ("XAUUSDT_1m.pkl", "2026-07-01", None)
CAP, EURUSD, STEP = vo.CAP, vo.EURUSD, vo.STEP


def htf_sma_trend(df, fast=9, slow=21, rule="1h", lookahead=False):
    hc = df.Close.resample(rule).last().dropna()
    f, s = hc.rolling(fast).mean(), hc.rolling(slow).mean()
    t = np.sign(f - s)
    if not lookahead: t = t.shift(1)                    # solo velas de H1 cerradas
    else: t = t.shift(0)                                # imita lookahead_on: usa el cierre final de la H1 en curso
    return t.reindex(df.index, method="ffill").fillna(0).values if not lookahead else \
        t.reindex(df.index.floor(rule)).fillna(0).values


def signals(df, fast=9, slow=21, lookahead=False):
    c = df.Close
    f, s = c.rolling(fast).mean(), c.rolling(slow).mean()
    up = (f > s) & (f.shift() <= s.shift()); dn = (f < s) & (f.shift() >= s.shift())
    ht = htf_sma_trend(df, fast, slow, lookahead=lookahead)
    return (up.values & (ht > 0)), (dn.values & (ht < 0))


def simulate(df, i0, cost=0.20, risk=0.01, max_risk=0.04, lookahead=False, rr=(1, 2, 3), swing=10):
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    L, S = signals(df, lookahead=lookahead)
    lo10 = pd.Series(l).rolling(swing).min().values; hi10 = pd.Series(h).rolling(swing).max().values
    eq = CAP; pos = 0; parts = []; stop = entry = 0.0; pend = None
    peak = CAP; mdd = 0.0; ntr = 0; wins = 0; gp = gl = 0.0; tp_ = 0.0; curve = []
    def close(px):
        nonlocal eq, pos, parts, tp_, ntr, wins, gp, gl
        for q, _ in parts:
            pnl = pos * q * (px - pos * cost - entry); eq += pnl; tp_ += pnl
        ntr += 1; wins += tp_ > 0; gp += max(tp_, 0); gl += max(-tp_, 0); tp_ = 0; pos = 0; parts = []
    for i in range(i0, len(c)):
        if pend is not None:
            d, sl = pend; pend = None
            if pos: close(o[i])
            px = o[i] + d * cost; dist = (px - sl) * d
            if dist > 0:
                q = np.floor(eq * risk / dist / STEP) * STEP
                if q < STEP: q = STEP if dist <= eq * max_risk else 0
                if q:
                    n = int(min(3, q)); base = np.floor(q / n); rest = q - base * n
                    sel = {1: [1], 2: [0, 2]}.get(n, [0, 1, 2])
                    parts = [[base + (k < rest), px + d * rr[t] * dist] for k, t in enumerate(sel)]
                    pos, entry, stop = d, px, sl
        if pos:
            if (pos == 1 and l[i] <= stop) or (pos == -1 and h[i] >= stop):
                close(min(o[i], stop) if pos == 1 else max(o[i], stop))
            else:
                keep = []
                for q, t in parts:
                    if (pos == 1 and h[i] >= t) or (pos == -1 and l[i] <= t):
                        pnl = pos * q * (t - pos * cost - entry); eq += pnl; tp_ += pnl
                    else: keep.append([q, t])
                parts = keep
                if not parts: parts = [[0, 0]]; close(c[i])
        cur = eq + (sum(pos * q * (c[i] - entry) for q, _ in parts) if pos else 0)
        peak = max(peak, cur); mdd = min(mdd, cur / peak - 1); curve.append(cur / EURUSD)
        if i + 1 < len(c):
            if L[i] and pos != 1: pend = (1, lo10[i])
            elif S[i] and pos != -1: pend = (-1, hi10[i])
    days = (df.index[-1] - df.index[i0]).days or 1; ret = cur / CAP - 1
    r = dict(trades=ntr, win=round(wins / ntr * 100) if ntr else 0, PF=round(gp / gl, 2) if gl else 0,
             monthly=round(((1 + ret) ** (30.4 / days) - 1) * 100, 2) if ret > -1 else -100, maxDD=round(mdd * 100, 1))
    return r, pd.Series(curve, index=df.index[i0:i0 + len(curve)])


def sniper_filter(df, L, S):
    t = htf_sma_trend(df)
    return L & (t > 0), S & (t < 0)


if __name__ == "__main__":
    pd.set_option("display.width", 250)
    rows = []
    for tf in ("M5", "M15", "M30", "H1"):
        for per in cb.PERS:
            df, i0 = vo.load(per, tf)
            for la in (False, True):
                r, _ = simulate(df, i0, lookahead=la)
                rows.append(dict(prueba="Sniper solo" + (" (con lookahead, como TradingView)" if la else ""), tf=tf, periodo=per, **r))
    for per in cb.PERS:
        for name, f in (("Actual", None), ("Actual + filtro Sniper H1", sniper_filter)):
            cs = []
            for acc in ACCOUNTS:
                cv, r = cb.cur_account(acc, per, sig_filter=f); cs.append(cv)
                rows.append(dict(prueba=name, tf=acc["id"], periodo=per, trades=r["trades"], PF=r["PF"], **cb.stats(cv, 1000)))
            rows.append(dict(prueba=name, tf="CARTERA 4", periodo=per, **cb.stats(cb.portfolio(cs), 4000)))
    res = pd.DataFrame(rows)
    print(res.pivot_table(index=["prueba", "tf"], columns="periodo", values=["monthly", "maxDD", "PF"], aggfunc="first")
          .reindex(columns=[(m, p) for m in ("monthly", "maxDD", "PF") for p in cb.PERS]).to_string())
    res.to_csv(cb.R / "sniper.csv", index=False)
