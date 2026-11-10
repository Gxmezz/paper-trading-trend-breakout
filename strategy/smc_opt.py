"""
Optimización de la estrategia SMC (strategy/smc.py) con la misma metodología que vantage_opt.py:
selección con dos periodos de entrenamiento (XAUUSDT dic-25..jun-26 y PAXG ene..nov-25) y comprobación
en un periodo que no interviene (XAUUSDT jul-26 -> último dato).

Uso: python smc_opt.py M5,M15,M30
"""
import itertools, sys, pandas as pd
from multiprocessing import Pool
from pathlib import Path
import vantage_opt as vo, smc

OUT = Path(__file__).parent / "results"
TFMIN = {"M5": 5, "M15": 15, "M30": 30}
vo.PERIODS["XAU_test"] = ("XAUUSDT_1m.pkl", "2026-07-01", None)


def grid(tf):
    G = []
    for n, nh, imp, zone, conf, sl, tp, sess, age_h in itertools.product(
            [2, 3, 5], [3, 5], [1.5, 2.5, 4.0], ["ob", "ob_ote", "fvg"], ["close", "brk"],
            [0.2, 0.5, 1.0], [(1, 2, 3), (2, 3, 4), (1.5, 3, 5), (2, 2, 2)], [(0, 24), (7, 20)], [24, 72]):
        G.append(dict(n=n, nh=nh, imp=imp, zone=zone, conf=conf, conf_win=max(3, 60 // TFMIN[tf]),
                      age=age_h * 60 // TFMIN[tf], sl=sl, tp=tp, news="block", sess=sess))
    return G


DATA, PRE = {}, {}
def init(tf):
    for per in vo.PERIODS: DATA[per] = vo.load(per, tf)

def pre_for(per, p):
    key = (per, p["n"], p["nh"], p["imp"], p["zone"], bool(p.get("ema_filter")))
    if key not in PRE:
        if len(PRE) > 64: PRE.clear()
        PRE[key] = smc.prepare(DATA[per][0], p)
    return PRE[key]

def job(args):
    tf, idx, p = args
    out = dict(tf=tf, idx=idx, **{k: str(v) if isinstance(v, tuple) else v for k, v in p.items()})
    for per in ("XAU_train", "PAXG_train"):
        df, i0 = DATA[per]
        r = smc.simulate(df, i0, p, pre=pre_for(per, p))
        out.update({f"{per}_{k}": v for k, v in r.items()})
    return out

def test_job(args):
    tf, idx, p, cost = args
    df, i0 = DATA["XAU_test"]
    return dict(tf=tf, idx=idx, cost=cost, **smc.simulate(df, i0, p, pre=pre_for("XAU_test", p), cost_side=cost))

def select(res, min_xau=20, min_paxg=30):
    for per in ("XAU_train", "PAXG_train"):
        res[f"{per}_score"] = res[f"{per}_monthly"] / res[f"{per}_maxDD"].abs().clip(lower=5)
    ok = res[(res.XAU_train_trades >= min_xau) & (res.PAXG_train_trades >= min_paxg) &
             (res.XAU_train_PF > 1.1) & (res.PAXG_train_PF > 1.1)].copy()
    ok["score"] = ok[["XAU_train_score", "PAXG_train_score"]].min(axis=1)
    return ok

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    pd.set_option("display.width", 300); pd.set_option("display.max_columns", 50)
    for tf in sys.argv[1].split(","):
        G = grid(tf)
        with Pool(8, initializer=init, initargs=(tf,)) as pool:
            # trozos contiguos: las combinaciones vecinas comparten zonas precalculadas
            res = pd.DataFrame(pool.map(job, [(tf, i, p) for i, p in enumerate(G)], chunksize=len(G) // 64 or 1))
            res.to_csv(OUT / f"smc_train_{tf}.csv", index=False)
            ok = select(res)
            top = ok.sort_values("score", ascending=False).head(10)
            tests = pool.map(test_job, [(tf, int(r.idx), G[int(r.idx)], c) for _, r in top.iterrows() for c in (0.20, 0.30)])
        print(f"\n=========== SMC {tf}: {len(ok)}/{len(res)} combinaciones rentables en ambos periodos ===========")
        if not len(top): continue
        t = pd.DataFrame(tests).pivot_table(index="idx", columns="cost", values=["ret", "PF", "maxDD", "trades", "monthly"], aggfunc="first")
        t.columns = [f"TEST_{a}@{b}" for a, b in t.columns]
        show = top.set_index("idx").join(t)
        cols = ["n", "nh", "imp", "zone", "conf", "sl", "tp", "sess", "age",
                "XAU_train_trades", "XAU_train_PF", "XAU_train_monthly", "XAU_train_maxDD",
                "PAXG_train_trades", "PAXG_train_PF", "PAXG_train_monthly", "PAXG_train_maxDD",
                "TEST_trades@0.2", "TEST_PF@0.2", "TEST_monthly@0.2", "TEST_maxDD@0.2", "TEST_PF@0.3", "TEST_monthly@0.3"]
        print(show[cols].to_string())
        show.to_csv(OUT / f"smc_top_{tf}.csv")
