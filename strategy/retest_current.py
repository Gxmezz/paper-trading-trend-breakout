"""Vuelve a medir las 4 estrategias del paper trading con datos actualizados (incluye datos posteriores a su elección)."""
import sys, pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
import vantage_opt as vo
from paper_trader import ACCOUNTS

TF = {"1h": "H1", "30m": "M30", "15m": "M15", "5m": "M5"}
vo.PERIODS.update({
    "XAU_test": ("XAUUSDT_1m.pkl", "2026-07-01", None),
    "XAU_last30": ("XAUUSDT_1m.pkl", "2026-10-10", None),
})
rows = []
for acc in ACCOUNTS:
    p = {k: v for k, v in acc.items() if k in ("fam", "htf", "ema", "don", "rsi", "lo", "hi", "mult", "units", "shorts", "sess")}
    for per in ("XAU_train", "PAXG_train", "XAU_test", "XAU_last30"):
        df, i0 = vo.load(per, TF[acc["tf"]])
        r = vo.simulate(df, i0, p, risk=acc["risk"])
        rows.append(dict(cuenta=acc["id"], periodo=per, **r))
print(pd.DataFrame(rows).to_string(index=False))
