"""
Palancas para aumentar la rentabilidad SIN añadir indicadores:
  - riesgo por operación, apalancamiento máximo
  - piramidación: añadir una unidad en cada nueva ruptura Donchian mientras la posición está abierta
  - cartera: oro + BTC a la vez sobre el mismo capital
Costes Vantage (Raw ECN) estimados + swaps (financiación) como estrés.
"""
import numpy as np, pandas as pd, itertools
import backtest as b

VANTAGE = {
    # oro: comisión 3$/lado/lote(100oz)=0.03$/oz + medio spread ~0.08 + slippage ~0.10 => ~0.21$/oz ≈ 0.005%. Usamos 0.012% (conservador)
    "GCF":     dict(side=0.00012),
    # BTC CFD: spread típico ~0.05-0.1% => medio spread + slippage ~0.08% por lado. Usamos 0.10%
    "BTC-USD": dict(side=0.0010),
}

def run_pyr(df, side_cost, fin_year, bpy, ema_len=200, don_len=20, atr_len=14, atr_mult=3.0,
            risk=0.01, max_lev=2.0, max_units=1):
    o,h,l,c = (df[k].values for k in ["Open","High","Low","Close"])
    ema = df.Close.ewm(span=ema_len, adjust=False).mean().values
    up = df.High.rolling(don_len).max().shift(1).values
    a = b.atr(df, atr_len).values
    n=len(df); cash=1.0; units=[]; stop=np.nan; pending=False
    eqc=np.empty(n); trades=[]; fin_bar=fin_year/bpy
    for i in range(n):
        if pending:
            px=o[i]*(1+side_cost); dist=atr_mult*a[i-1]
            eq_now = cash + sum(q*(o[i]-e) for q,e in units)
            exposure = sum(q for q,_ in units)*o[i]
            q = min(eq_now*risk/dist, max(0.0,(eq_now*max_lev-exposure))/px)
            if q>0:
                units.append((q,px)); ns=px-dist
                stop = ns if np.isnan(stop) else max(stop, ns)
            pending=False
        if units:
            if l[i] <= stop:
                fill=min(o[i],stop)*(1-side_cost)
                pnl=sum(q*(fill-e) for q,e in units); cash+=pnl
                trades.append(pnl); units=[]; stop=np.nan
            else:
                cash -= sum(q for q,_ in units)*c[i]*fin_bar
                stop=max(stop, c[i]-atr_mult*a[i])
        eqc[i]=cash+sum(q*(c[i]-e) for q,e in units)
        if i>=ema_len and i+1<n and c[i]>up[i] and c[i]>ema[i] and len(units)<max_units:
            pending=True
    return pd.Series(eqc, df.index), pd.DataFrame(dict(pnl=trades, r=np.array(trades)))

def st(eq, bpy):
    yrs=len(eq)/bpy; dd=(eq/eq.cummax()-1).min(); r=eq.pct_change().dropna()
    cagr=eq.iloc[-1]**(1/yrs)-1
    return dict(CAGR=round(cagr*100,1), maxDD=round(dd*100,1),
                MAR=round(cagr/-dd,2) if dd<0 else np.nan,
                sharpe=round(r.mean()/r.std()*np.sqrt(bpy),2))

if __name__=="__main__":
    pd.set_option("display.width",200)
    data={"GCF":(b.load("GCF","1d"),252,0.06),"BTC-USD":(b.load("BTC-USD","1d"),365,0.15)}
    rows=[]
    for sym,(df,bpy,fin) in data.items():
        for risk,lev,units in itertools.product([0.01,0.02,0.03],[2,5],[1,3]):
            for per,sub in [("ALL",df),("2020+",df["2020":])]:
                eq,tr=run_pyr(sub,VANTAGE[sym]["side"],fin,bpy,risk=risk,max_lev=lev,max_units=units)
                rows.append(dict(sym=sym,period=per,risk=risk,lev=lev,units=units,trades=len(tr),**st(eq,bpy)))
    r=pd.DataFrame(rows); print(r.to_string(index=False))
    # Cartera combinada (oro + BTC, mismo capital, días alineados)
    print("\nCARTERA ORO+BTC (swaps oro 6%/año, BTC 15%/año)")
    out=[]
    for risk,units in itertools.product([0.01,0.02,0.03],[1,3]):
        for per in ["ALL","2020+"]:
            rets=[]
            for sym,(df,bpy,fin) in data.items():
                sub=df if per=="ALL" else df["2020":]
                sub=sub["2014-09-17":]
                eq,_=run_pyr(sub,VANTAGE[sym]["side"],fin,bpy,risk=risk,max_lev=5,max_units=units)
                eq.index=eq.index.normalize(); rets.append(eq.pct_change().rename(sym))
            R=pd.concat(rets,axis=1).fillna(0); peq=(1+R.sum(axis=1)).cumprod()
            out.append(dict(period=per,risk=risk,units=units,**st(peq,365)))
    print(pd.DataFrame(out).to_string(index=False))
