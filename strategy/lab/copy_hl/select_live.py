"""Selección mensual para el paper trading de copia (mismo procedimiento que el examen).

El día 1 de cada mes (workflow "Copy HL · selección mensual"):
  1. Muestra al azar de 1000 carteras del ranking actual (semilla = AAAAMM).
  2. Descarga sus operaciones y curvas, y los precios de 1 min de los 9 últimos ~8 meses.
  3. Simula la copia de cada una (retraso 60 s, costes, funding, apalancamiento 3x).
  4. Elige 10 con S2 (rentabilidad/caída de su copia en 180 días, w = 0, congelado en el
     examen) y 10 al azar entre las copiables (cuenta de control).
  -> escribe seleccion.json, que lee copy_trader.py.

Uso:  python strategy/lab/copy_hl/select_live.py            (todo, ~4 h por el límite de la API)
      python strategy/lab/copy_hl/select_live.py --score    (solo el paso 4 con los datos ya descargados)
"""
import json, os, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
OUT = HERE / "seleccion.json"
COINS = ["BTC", "ETH", "SOL", "HYPE", "XRP", "ZEC", "kPEPE", "FARTCOIN", "DOGE"]  # fijas desde el examen
K = 10


def score(month):
    sys.path.insert(0, str(HERE))
    import numpy as np
    import exam as E
    d = E.Data()
    t = E.NH - 1
    s = d.scores(t)
    top = E.ranked(d, t, "S2", w=0.0)[:K]
    rng = np.random.default_rng(int(month.replace("-", "")))
    pool = list(s.index)
    rnd = list(rng.choice(pool, size=min(K, len(pool)), replace=False)) if pool else []
    row = lambda i: dict(addr=d.ws[i]["addr"], ret180=float(s.loc[i, "retdd"] * max(s.loc[i, "dd"], 0.01)),
                         dd180=float(s.loc[i, "dd"]), retdd=float(s.loc[i, "retdd"]), sharpe=float(s.loc[i, "sharpe"]))
    out = dict(month=month, created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               datos_hasta=os.environ.get("COPYHL_END", "2026-11-16"), n_muestra=len(d.ws), n_copiables=len(pool),
               coins=COINS, copia=[row(i) for i in top], azar=[row(i) for i in rnd])
    OUT.write_text(json.dumps(out, indent=1))
    print(f"{month}: {len(pool)} copiables; copia {len(top)}, azar {len(rnd)} -> {OUT}")


def main():
    now = datetime.now(timezone.utc)
    month = os.environ.get("COPYHL_MONTH", f"{now:%Y-%m}")
    if "--score" in sys.argv:
        return score(month)
    end = now.date()
    env = dict(os.environ, COPYHL_DATA=str(ROOT / "strategy" / "data" / f"hl_live_{month}"),
               COPYHL_END=end.isoformat(), COPYHL_SEED=month.replace("-", ""),
               COPYHL_PX_START=(end - timedelta(days=240)).isoformat(), COPYHL_MONTH=month)
    Path(env["COPYHL_DATA"]).mkdir(parents=True, exist_ok=True)
    (Path(env["COPYHL_DATA"]) / "coins.json").write_text(json.dumps(COINS))
    py = sys.executable
    for args in (["download_hl.py", "1000"], ["download_prices.py"], ["build.py", "series"],
                 ["select_live.py", "--score"]):
        print(">>", *args, flush=True)
        subprocess.run([py, "-u", str(HERE / args[0]), *args[1:]], env=env, check=True)


if __name__ == "__main__":
    main()
