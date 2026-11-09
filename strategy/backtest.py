"""
Backtest local: Tendencia EMA + ruptura Donchian + stop ATR (trailing).
Costes incluidos: spread + slippage + comision (por lado) y financiacion overnight.
Ejecucion: senal al cierre de la vela, entrada al open de la siguiente (sin look-ahead).
Stops: se comprueban intrabar con high/low; si hay gap, se ejecuta al open (peor precio).
"""
import sys, numpy as np, pandas as pd
from pathlib import Path

D = Path(__file__).parent / "data"

COSTS = {
    # coste por lado en fraccion del precio (spread/2 + slippage + comision)
    # Oro: spread ~0.30$, slippage ~0.10$, comision ~0.035$/oz/lado -> ~0.30$/lado (~0.008% a 3800$). Usamos 0.012% (conservador)
    "GCF":    dict(side=0.00012, fin_year=0.05),
    # BTC: comision taker 0.06% + spread/slippage 0.04% -> 0.10% por lado (conservador)
    "BTC-USD": dict(side=0.0010, fin_year=0.10),
}

def load(sym, tf):
    if tf == "1d":
        df = pd.read_csv(D / f"{sym}_1d.csv", index_col=0, parse_dates=True)
    else:
        df = pd.read_csv(D / f"{sym}_1h.csv", index_col=0)
        df.index = pd.to_datetime(df.index, utc=True)
        df = df.resample(tf).agg({"Open":"first","High":"max","Low":"min","Close":"last"}).dropna()
    df = df[["Open","High","Low","Close"]].dropna()
    return df[df["Close"] > 0]

def atr(df, n):
    pc = df.Close.shift()
    tr = pd.concat([df.High-df.Low, (df.High-pc).abs(), (df.Low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()  # RMA, igual que ta.atr de TradingView

def run(df, sym, ema_len=200, don_len=20, atr_len=14, atr_mult=3.0, risk=0.01,
        side_cost=None, fin_year=None, longs=True, shorts=True, bars_per_year=365):
    c = COSTS[sym]
    side_cost = c["side"] if side_cost is None else side_cost
    fin_year = c["fin_year"] if fin_year is None else fin_year
    o,h,l,cl = (df[k].values for k in ["Open","High","Low","Close"])
    ema = df.Close.ewm(span=ema_len, adjust=False).mean().values
    up = df.High.rolling(don_len).max().shift(1).values   # maximo de las N velas PREVIAS
    dn = df.Low.rolling(don_len).min().shift(1).values
    a = atr(df, atr_len).values
    n = len(df); eq = 1.0; pos = 0; qty = 0.0; stop = np.nan; entry = 0.0
    equity = np.empty(n); trades = []; pending = 0
    fin_bar = fin_year / bars_per_year
    for i in range(n):
        # 1) ejecutar entrada pendiente al open
        if pending != 0 and pos == 0:
            px = o[i] * (1 + side_cost*pending)
            stop_dist = atr_mult * a[i-1]
            qty = (eq * risk) / stop_dist           # tamano por riesgo fijo
            qty = min(qty, eq * 2 / px)             # apalancamiento maximo 2x
            pos = pending; entry = px; stop = px - pos*stop_dist
            eq -= 0  # coste ya incluido en precio
            ent_eq = eq; pending = 0
        # 2) gestionar posicion: stop intrabar
        if pos != 0:
            hit = (pos == 1 and l[i] <= stop) or (pos == -1 and h[i] >= stop)
            if hit:
                fill = min(o[i], stop) if pos == 1 else max(o[i], stop)
                fill *= (1 - side_cost*pos)
                pnl = pos * qty * (fill - entry)
                eq += pnl
                trades.append(dict(t=df.index[i], dir=pos, pnl=pnl, r=pnl/(ent_eq*risk)))
                pos = 0; qty = 0
            else:
                eq -= qty * cl[i] * fin_bar          # financiacion overnight (long y short)
                # trailing stop (chandelier) al cierre, solo se mueve a favor
                ns = cl[i] - pos*atr_mult*a[i]
                stop = max(stop, ns) if pos == 1 else min(stop, ns)
        equity[i] = eq + (pos*qty*(cl[i]-entry) if pos else 0)
        # 3) senal al cierre -> entrada en el open siguiente
        if pos == 0 and i >= max(ema_len, don_len) and i+1 < n:
            if longs and cl[i] > up[i] and cl[i] > ema[i]: pending = 1
            elif shorts and cl[i] < dn[i] and cl[i] < ema[i]: pending = -1
    return pd.Series(equity, df.index), pd.DataFrame(trades)

def stats(eq, tr, bars_per_year):
    yrs = len(eq)/bars_per_year
    cagr = eq.iloc[-1]**(1/yrs)-1
    dd = (eq/eq.cummax()-1).min()
    rets = eq.pct_change().dropna()
    sharpe = rets.mean()/rets.std()*np.sqrt(bars_per_year) if rets.std() > 0 else 0
    if len(tr)==0: return dict(trades=0)
    w = tr[tr.pnl>0]; ls = tr[tr.pnl<=0]
    pf = w.pnl.sum()/-ls.pnl.sum() if len(ls) else np.inf
    return dict(trades=len(tr), win=round(len(w)/len(tr)*100,1), PF=round(pf,2),
                avgR=round(tr.r.mean(),2), CAGR=round(cagr*100,1), maxDD=round(dd*100,1),
                sharpe=round(sharpe,2), final=round(eq.iloc[-1],2))

if __name__ == "__main__":
    pd.set_option("display.width", 200)
    rows = []
    for sym, bpy in [("GCF", 252), ("BTC-USD", 365)]:
        df = load(sym, "1d")
        for split, sub in [("ALL", df), ("IS<2020", df[:"2019"]), ("OOS>=2020", df["2020":])]:
            for mode, L, S in [("long+short",1,1),("long only",1,0)]:
                for cost_mult in [0, 1, 2]:
                    c = COSTS[sym]
                    eq, tr = run(sub, sym, longs=L, shorts=S, bars_per_year=bpy,
                                 side_cost=c["side"]*cost_mult, fin_year=c["fin_year"]*cost_mult)
                    rows.append(dict(sym=sym, tf="1D", period=split, mode=mode, costs=f"x{cost_mult}", **stats(eq, tr, bpy)))
    print(pd.DataFrame(rows).to_string(index=False))
