"""Fase 0: descarga de datos de Hyperliquid para el examen de copy trading.

- Ranking público completo (incluye carteras que pierden o se han quedado a cero).
- Muestra AL AZAR de carteras con volumen total de 10 M a 5 000 M $. No se filtra
  por beneficio para no meter sesgo del superviviente ni mirar el futuro.
- Por cartera: curva de beneficio (portfolio, puntos ~semanales) y operaciones
  (userFillsByTime; la API solo guarda las 10 000 últimas).

Reanudable: si se corta, se vuelve a lanzar y sigue donde iba.
Uso:  python strategy/lab/copy_hl/download_hl.py [N=1500]
"""
import json, os, pickle, random, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("COPYHL_DATA", ROOT / "strategy" / "data" / "hl"))
WAL = OUT / "wallets"
INFO = "https://api.hyperliquid.xyz/info"
LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
SEED = int(os.environ.get("COPYHL_SEED", 42))
VLM_MIN, VLM_MAX = 1e7, 5e9

S = requests.Session()


def post(payload, tries=8):
    for i in range(tries):
        try:
            r = S.post(INFO, json=payload, timeout=60)
            if r.status_code == 429:
                time.sleep(5 * (i + 1)); continue
            r.raise_for_status()
            out = r.json()
            # límite: 1200 de peso/min; cada petición pesa 20 + 1 por cada 20 elementos
            n = len(out) if isinstance(out, list) and payload["type"] == "userFillsByTime" else 0
            time.sleep((20 + n / 20) / 20 * 1.1)
            return out
        except requests.RequestException:
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"fallo persistente {payload['type']}")


def leaderboard():
    f = OUT / "leaderboard.json"
    if not f.exists():
        f.write_text(json.dumps(S.get(LB, timeout=120).json()["leaderboardRows"]))
    return json.loads(f.read_text())


def fills(addr):
    out, start = [], 0
    for _ in range(10):  # 10 000 / 2000 = 5 páginas como máximo
        page = post({"type": "userFillsByTime", "user": addr, "startTime": start,
                     "aggregateByTime": True})
        if not page:
            break
        out += page
        if len(page) < 2000:
            break
        start = page[-1]["time"] + 1
    seen, uniq = set(), []
    for x in out:
        k = (x["tid"], x["oid"], x["time"])
        if k not in seen:
            seen.add(k); uniq.append(x)
    return uniq


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    WAL.mkdir(parents=True, exist_ok=True)
    rows = leaderboard()
    pool = []
    for r in rows:
        w = dict(r["windowPerformances"])
        if VLM_MIN <= float(w["allTime"]["vlm"]) <= VLM_MAX:
            pool.append(r["ethAddress"])
    pool.sort()
    random.Random(SEED).shuffle(pool)
    sample = pool[:n]
    (OUT / "sample.json").write_text(json.dumps(sample))
    print(f"ranking {len(rows)} | candidatas {len(pool)} | muestra {len(sample)}", flush=True)
    t0 = time.time()
    for i, a in enumerate(sample):
        f = WAL / f"{a}.pkl"
        if f.exists():
            continue
        port = dict(post({"type": "portfolio", "user": a}))
        d = {"addr": a, "portfolio": port.get("perpAllTime") or port.get("allTime"),
             "fills": fills(a), "ts": int(time.time() * 1000)}
        tmp = f.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps(d)); tmp.replace(f)
        if i % 10 == 0:
            print(f"{i+1}/{len(sample)} {len(d['fills'])} fills  {(time.time()-t0)/60:.1f} min", flush=True)
    print("hecho", flush=True)


if __name__ == "__main__":
    main()
