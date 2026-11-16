"""Precios de 1 minuto (Binance futuros, data.binance.vision) y funding horario de
Hyperliquid, para simular la copia con retraso.

El perp de Hyperliquid sigue al de Binance muy de cerca; se usa Binance porque
Hyperliquid solo da las 5000 últimas velas por intervalo.

Uso:  python strategy/lab/copy_hl/download_prices.py BTC ETH SOL ...
      (sin argumentos: las monedas de coins.json, que genera build_dataset.py)
"""
import io, json, os, pickle, sys, time, zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("COPYHL_DATA", ROOT / "strategy" / "data" / "hl"))
PX = OUT / "px"
VISION = "https://data.binance.vision/data/futures/um"
INFO = "https://api.hyperliquid.xyz/info"
START = date.fromisoformat(os.environ.get("COPYHL_PX_START", "2024-01-01"))

ALIAS = {"kPEPE": "1000PEPE", "kSHIB": "1000SHIB", "kBONK": "1000BONK",
         "kFLOKI": "1000FLOKI", "kLUNC": "1000LUNC", "kDOGS": "1000DOGS",
         "kNEIRO": "1000NEIRO"}


def bsym(coin):
    return ALIAS.get(coin, coin) + "USDT"


def months(d0, d1):
    y, m = d0.year, d0.month
    while (y, m) < (d1.year, d1.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def read_zip(content):
    z = zipfile.ZipFile(io.BytesIO(content))
    raw = z.read(z.namelist()[0]).decode()
    lines = [l for l in raw.splitlines() if l and l[0].isdigit()]
    df = pd.DataFrame([l.split(",")[:5] for l in lines], columns=["t", "o", "h", "l", "c"])
    df = df.astype({"t": "int64", "o": float, "h": float, "l": float, "c": float})
    return df


def klines(coin):
    f = PX / f"{coin}.pkl"
    if f.exists():
        return
    sym, parts = bsym(coin), []
    today = datetime.now(timezone.utc).date()
    urls = [f"{VISION}/monthly/klines/{sym}/1m/{sym}-1m-{y}-{m:02d}.zip"
            for y, m in months(START, today.replace(day=1))]
    urls += [f"{VISION}/daily/klines/{sym}/1m/{sym}-1m-{d:%Y-%m-%d}.zip"
             for d in pd.date_range(today.replace(day=1), today, inclusive="left")]

    def get(u):
        for i in range(4):
            try:
                r = requests.get(u, timeout=120)
                return read_zip(r.content) if r.status_code == 200 else None
            except requests.RequestException:
                time.sleep(3)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        parts = [p for p in ex.map(get, urls) if p is not None]
    if not parts:
        print(coin, "sin datos en Binance"); return
    df = pd.concat(parts).drop_duplicates("t").sort_values("t").set_index("t")
    if coin in ALIAS:  # precios de 1000X -> precio de kX en Hyperliquid (mismo factor 1000)
        pass
    pickle.dump(df, open(f, "wb"))
    print(coin, len(df), pd.to_datetime(df.index[0], unit="ms"), "->", pd.to_datetime(df.index[-1], unit="ms"), flush=True)


def funding(coin):
    f = PX / f"{coin}_funding.pkl"
    if f.exists():
        return
    start, out = int(datetime.combine(START, datetime.min.time(), timezone.utc).timestamp() * 1000), []
    while True:
        for i in range(6):
            r = requests.post(INFO, json={"type": "fundingHistory", "coin": coin, "startTime": start}, timeout=60)
            if r.status_code == 200:
                break
            time.sleep(5 * (i + 1))
        page = r.json() if r.status_code == 200 else []
        time.sleep(0.6)
        if not page:
            break
        out += page
        if len(page) < 500:
            break
        start = page[-1]["time"] + 1
    s = pd.Series({x["time"]: float(x["fundingRate"]) for x in out}).sort_index()
    pickle.dump(s, open(f, "wb"))


def main():
    PX.mkdir(parents=True, exist_ok=True)
    coins = sys.argv[1:] or json.loads((OUT / "coins.json").read_text())
    nofund = "--sin-funding" in coins
    coins = [c for c in coins if not c.startswith("--")]
    for c in coins:
        klines(c)
        if not nofund:
            funding(c)
    print("hecho")


if __name__ == "__main__":
    main()
