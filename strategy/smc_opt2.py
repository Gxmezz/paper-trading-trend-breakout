"""
Optimización de la SMC v2 (smc.py con barrida de liquidez, premium/descuento, killzones, objetivos en liquidez,
trailing, sesgo H4, alineación de estructura y filtros EMA / Sniper).

Selección SOLO con los dos periodos de entrenamiento (XAUUSDT dic-25..jun-26 y PAXG ene..nov-25).
Fases siguientes (otros comandos, en este orden):
  python smc_opt2.py train M5,M15     -> results/smc2_train_<TF>.csv
  python smc_opt2.py test  M5,M15     -> top 20 de entrenamiento medidos en XAUUSDT jul-26 -> último dato
  python smc_opt2.py final <TF>:<idx> -> examen final en PAXG jul..dic-2024 (datos nunca usados)
"""
import itertools, sys, pandas as pd
from multiprocessing import Pool
from pathlib import Path
import vantage_opt as vo, smc, smc_opt

OUT = Path(__file__).parent / "results"
TFMIN = {"M5": 5, "M15": 15}
vo.PERIODS["XAU_test"] = ("XAUUSDT_1m.pkl", "2026-07-01", None)
vo.PERIODS["PAXG_final"] = ("PAXGUSDT_1m.pkl", "2024-07-01", "2024-12-31")
EXITS = {"R234": dict(tp=(2, 3, 4)), "R1535": dict(tp=(1.5, 3, 5)), "liq": dict(tp=(1, 2, 3), tpm="liq"),
         "R1535trail": dict(tp=(1.5, 3, 5), trail=True)}


def grid(tf):
    G = []
    for n, nh, imp, zone, sweep, bias, ltf, filt in itertools.product(
            [3, 5], [3, 5], [1.5, 2.5], ["ob", "fvg"], [0, 20], ["1h", "4h"], [False, True], [None, "sniper", "ema"]):
        for conf, sl, pd_, sess, ex in itertools.product(["brk", "close"], [0.2, 0.5, 1.0], [False, True],
                                                         [(0, 24), "kz"], list(EXITS)):
            G.append(dict(n=n, nh=nh, imp=imp, zone=zone, sweep=sweep, bias=bias, ltf_align=ltf, filt=filt,
                          conf=conf, conf_win=max(3, 60 // TFMIN[tf]), age=24 * 60 // TFMIN[tf], sl=sl, pd=pd_,
                          sess=sess, exit=ex, news="block", **EXITS[ex]))
    return G


DATA, PRE = {}, {}
def init(tf, pers):
    for per in pers: DATA[per] = vo.load(per, tf)

def pre_for(per, p):
    key = (per, p["n"], p["nh"], p["imp"], p["zone"], p["sweep"], p["bias"], p["ltf_align"], p["filt"])
    if key not in PRE:
        if len(PRE) > 16: PRE.clear()
        PRE[key] = smc.prepare(DATA[per][0], p)
    return PRE[key]

def run(per, p, cost=0.20):
    df, i0 = DATA[per]
    return smc.simulate(df, i0, p, pre=pre_for(per, p), cost_side=cost)

def job(args):
    tf, idx, p = args
    out = dict(tf=tf, idx=idx, **{k: str(v) if isinstance(v, tuple) else v for k, v in p.items()})
    for per in ("XAU_train", "PAXG_train"):
        out.update({f"{per}_{k}": v for k, v in run(per, p).items()})
    return out

def test_job(args):
    tf, idx, p, per, cost = args
    return dict(tf=tf, idx=idx, per=per, cost=cost, **run(per, p, cost))

if __name__ == "__main__":
    mode, arg = sys.argv[1], sys.argv[2]
    OUT.mkdir(exist_ok=True); pd.set_option("display.width", 320); pd.set_option("display.max_columns", 60)
    if mode == "train":
        for tf in arg.split(","):
            G = grid(tf)
            with Pool(8, initializer=init, initargs=(tf, ("XAU_train", "PAXG_train"))) as pool:
                res = pd.DataFrame(pool.map(job, [(tf, i, p) for i, p in enumerate(G)], chunksize=len(G) // 384))
            res.to_csv(OUT / f"smc2_train_{tf}.csv", index=False)
            ok = smc_opt.select(res)
            print(f"{tf}: {len(ok)}/{len(res)} combinaciones rentables en ambos periodos de entrenamiento", flush=True)
            ok.sort_values("score", ascending=False).head(20).to_csv(OUT / f"smc2_top_{tf}.csv", index=False)
    elif mode == "test":
        for tf in arg.split(","):
            G = grid(tf); top = pd.read_csv(OUT / f"smc2_top_{tf}.csv")
            with Pool(8, initializer=init, initargs=(tf, ("XAU_test",))) as pool:
                t = pd.DataFrame(pool.map(test_job, [(tf, int(i), G[int(i)], "XAU_test", c) for i in top.idx for c in (0.2, 0.3)]))
            t = t.pivot_table(index="idx", columns="cost", values=["trades", "PF", "monthly", "maxDD"], aggfunc="first")
            t.columns = [f"TEST_{a}@{b}" for a, b in t.columns]
            show = top.set_index("idx").join(t)
            cols = ["n", "nh", "imp", "zone", "sweep", "bias", "ltf_align", "filt", "conf", "sl", "pd", "sess", "exit",
                    "XAU_train_trades", "XAU_train_monthly", "XAU_train_maxDD", "PAXG_train_trades", "PAXG_train_monthly",
                    "PAXG_train_maxDD", "score", "TEST_trades@0.2", "TEST_PF@0.2", "TEST_monthly@0.2", "TEST_maxDD@0.2", "TEST_monthly@0.3"]
            print(f"\n===== {tf} =====\n" + show[cols].to_string())
            show.to_csv(OUT / f"smc2_test_{tf}.csv")
    elif mode == "final":
        for spec in arg.split(","):
            tf, idx = spec.split(":"); p = grid(tf)[int(idx)]
            init(tf, ("PAXG_final",))
            print(tf, idx, p, "\nEXAMEN FINAL PAXG jul-dic 2024:", run("PAXG_final", p))
