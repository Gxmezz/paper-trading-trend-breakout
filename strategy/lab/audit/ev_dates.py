"""Auditoría ev_macro_drift: fechas NFP/FOMC contra los datos (la vela M30 del dato debe ser anormalmente amplia) y hora NY/DST."""
import sys, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).resolve().parent.parent; sys.path[:0] = [str(H), str(H / "ideas")]
import engine as E, ev_macro as M
df, _ = E.load("2020-01-01", "2025-12-31", "M30", warmup_days=0)
assert df.index[-1] < pd.Timestamp("2026-01-01", tz="UTC")
rg = (df.High - df.Low)
for kind in ("nfp", "fomc"):
    ev = M.events(kind); rows = []
    for e in ev:
        bar = e.floor("30min")
        if bar not in rg.index: rows.append(dict(e=e, ok=None)); continue
        day = rg[bar - pd.Timedelta(hours=12): bar + pd.Timedelta(hours=12)]
        # offsets: ¿la vela más amplia está desplazada (error de zona horaria)?
        offs = {k: rg.get(bar + pd.Timedelta(hours=k), np.nan) / day.median() for k in (-2, -1, 0, 1, 2)}
        rows.append(dict(e=e.tz_convert(M.NY), ratio=rg[bar] / day.median(), rank=int((day > rg[bar]).sum()) + 1, **{f"o{k}": v for k, v in offs.items()}))
    r = pd.DataFrame(rows)
    print(f"\n=== {kind}: {len(ev)} eventos, sin vela {r.ratio.isna().sum()} ===")
    print("ratio rango/mediana ±12h: mediana", r.ratio.median().round(2), " rank 1-2:", (r["rank"] <= 2).mean().round(2), " rank<=3:", (r["rank"] <= 3).mean().round(2))
    print("ratio medio por desplazamiento horario (h):", {k: round(r[f"o{k}"].median(), 2) for k in (-2, -1, 0, 1, 2)})
    # verano/invierno por separado (DST EE. UU. vs UE: semanas de marzo/oct-nov)
    dst = r.e.apply(lambda t: bool(t.dst()) if pd.notna(t) else None)
    for flag in (True, False): print("  DST NY", flag, "n", (dst == flag).sum(), "ratio mediano", r[dst == flag].ratio.median().round(2))
    print("eventos sospechosos (rank>3 o ratio<2):"); print(r[(r["rank"] > 3) | (r.ratio < 2)].round(2).to_string(index=False))
