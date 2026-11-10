"""
Compara cómo juntar la estrategia actual (4 cuentas EMA/Donchian/RSI) con la SMC:
  A  SMC sola (mejor configuración de smc_opt.py por temporalidad)
  B  Estrategia actual sola (las 4 cuentas del paper trading)
  C  SMC + filtro EMA 200 de H1 (solo entra si estructura y EMA coinciden)
  D  Cartera: las 4 cuentas actuales + la(s) cuenta(s) SMC, cada una con 1000 €
  E  Estrategia actual filtrada con el sesgo de estructura SMC de H1 (solo opera a favor de la estructura)
Periodos: los dos de entrenamiento y el de prueba (jul-26 -> último dato), que no interviene en la selección.

Uso: python combine.py "M15:idx,M30:idx"   (índices de la tabla de smc_opt; vacío = el mejor de cada TF)
"""
import ast, sys, numpy as np, pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from pathlib import Path
import vantage_opt as vo, smc, smc_opt
from paper_trader import ACCOUNTS

R = Path(__file__).parent / "results"
vo.PERIODS["XAU_test"] = ("XAUUSDT_1m.pkl", "2026-07-01", None)
TF = {"1h": "H1", "30m": "M30", "15m": "M15", "5m": "M5"}
PERS = ("XAU_train", "PAXG_train", "XAU_test")
_cache = {}

def load(per, tf):
    if (per, tf) not in _cache: _cache[(per, tf)] = vo.load(per, tf)
    return _cache[(per, tf)]

def stats(curve, cap):
    """curve: Serie de capital (€) indexada por tiempo."""
    d = curve.resample("1D").last().dropna()
    days = (d.index[-1] - d.index[0]).days or 1
    ret = d.iloc[-1] / cap - 1
    dd = (d / d.cummax() - 1).min()
    return dict(ret=round(ret * 100, 1), monthly=round(((1 + ret) ** (30.4 / days) - 1) * 100, 2),
                maxDD=round(dd * 100, 1), score=round(((1 + ret) ** (30.4 / days) - 1) * 100 / max(abs(dd * 100), 5), 3))

def cur_account(acc, per, sig_filter=None):
    df, i0 = load(per, TF[acc["tf"]])
    p = {k: v for k, v in acc.items() if k in ("fam", "htf", "ema", "don", "rsi", "lo", "hi", "mult", "units", "shorts", "sess")}
    cv = []
    r = vo.simulate(df, i0, p, risk=acc["risk"], curve=cv, sig_filter=sig_filter)
    return pd.Series(cv, index=df.index[i0:i0 + len(cv)]), r

def smc_account(tf, p, per):
    df, i0 = load(per, tf)
    cv = []
    r = smc.simulate(df, i0, p, curve=cv)
    return pd.Series(cv, index=df.index[i0:i0 + len(cv)]), r

def struct_filter(nh):
    def f(df, L, S):
        b = smc.htf_trend(df, "1h", nh)
        return L & (b == 1), S & (b == -1)
    return f

def best_params(spec):
    out = []
    for tf in ("M5", "M15", "M30"):
        f = R / f"smc_top_{tf}.csv"
        if not f.exists(): continue
        t = pd.read_csv(f)
        if spec and tf not in spec: continue
        row = t[t.idx == spec[tf]].iloc[0] if spec else t.iloc[0]
        G = smc_opt.grid(tf)
        out.append((tf, G[int(row.idx)]))
    return out

def portfolio(curves):
    idx = sorted(set().union(*[c.index for c in curves]))
    df = pd.concat([c.reindex(idx).ffill().bfill() for c in curves], axis=1)
    return df.sum(axis=1)

if __name__ == "__main__":
    spec = dict(x.split(":") for x in sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1] else {}
    spec = {k: int(v) for k, v in spec.items()}
    smcs = best_params(spec)
    rows = []
    for per in PERS:
        cur_curves = []
        for acc in ACCOUNTS:
            cv, r = cur_account(acc, per); cur_curves.append(cv)
            rows.append(dict(opcion="B actual", cuenta=acc["id"], periodo=per, trades=r["trades"], PF=r["PF"], **stats(cv, 1000)))
        rows.append(dict(opcion="B actual", cuenta="CARTERA 4", periodo=per, **stats(portfolio(cur_curves), 4000)))
        for nh in (3, 5):
            ec = []
            for acc in ACCOUNTS:
                cv, r = cur_account(acc, per, sig_filter=struct_filter(nh)); ec.append(cv)
                rows.append(dict(opcion=f"E actual+estructura{nh}", cuenta=acc["id"], periodo=per, trades=r["trades"], PF=r["PF"], **stats(cv, 1000)))
            rows.append(dict(opcion=f"E actual+estructura{nh}", cuenta="CARTERA 4", periodo=per, **stats(portfolio(ec), 4000)))
        smc_curves = []
        for tf, p in smcs:
            cv, r = smc_account(tf, p, per); smc_curves.append(cv)
            rows.append(dict(opcion="A SMC", cuenta=f"smc_{tf}", periodo=per, trades=r["trades"], PF=r["PF"], **stats(cv, 1000)))
            p2 = dict(p, ema_filter=True)
            cv2, r2 = smc_account(tf, p2, per)
            rows.append(dict(opcion="C SMC+EMA200", cuenta=f"smc_{tf}", periodo=per, trades=r2["trades"], PF=r2["PF"], **stats(cv2, 1000)))
        if smc_curves:
            rows.append(dict(opcion="D cartera actual+SMC", cuenta=f"{4 + len(smc_curves)} cuentas", periodo=per,
                             **stats(portfolio(cur_curves + smc_curves), 1000 * (4 + len(smc_curves)))))
    res = pd.DataFrame(rows)
    pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
    piv = res.pivot_table(index=["opcion", "cuenta"], columns="periodo", values=["monthly", "maxDD", "score"], aggfunc="first")
    piv = piv.reindex(columns=[(m, p) for m in ("monthly", "maxDD", "score") for p in PERS])
    print(piv.to_string())
    res.to_csv(R / "combine.csv", index=False)
    print("\nSMC usadas:", smcs)
