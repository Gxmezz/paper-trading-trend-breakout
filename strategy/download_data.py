"""
Descarga las velas de 1 minuto que usan los backtests (data.binance.vision, público y gratuito).

  XAUUSDT_1m.pkl : perpetuo de oro de Binance (dic-2025 -> ayer)
  PAXGUSDT_1m.pkl: PAXG spot (token respaldado por oro), jul-2024 -> nov-2025

Uso: python strategy/download_data.py
"""
import io, zipfile, datetime as dt
from pathlib import Path
import pandas as pd, requests

D = Path(__file__).parent / "data"
BASE = "https://data.binance.vision/data"
SETS = {
    "XAUUSDT_1m.pkl": ("futures/um", "XAUUSDT", dt.date(2025, 12, 1), None),
    "PAXGUSDT_1m.pkl": ("spot", "PAXGUSDT", dt.date(2024, 6, 1), dt.date(2025, 11, 30)),
}

def fetch(url):
    r = requests.get(url, timeout=60)
    if r.status_code == 404: return None
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    raw = z.read(z.namelist()[0]).decode()
    df = pd.read_csv(io.StringIO(raw), header=None, usecols=range(5))
    if not str(df.iloc[0, 0]).isdigit(): df = df.iloc[1:]          # cabecera en ficheros nuevos
    df = df.astype(float)
    t = df[0].astype("int64")
    t = t.where(t < 10**14, t // 1000)                             # microsegundos -> ms (spot desde 2025)
    out = pd.DataFrame({"Open": df[1].values, "High": df[2].values, "Low": df[3].values, "Close": df[4].values},
                       index=pd.to_datetime(t.values, unit="ms", utc=True))
    return out

def download(market, sym, start, end):
    end = end or (dt.date.today() - dt.timedelta(days=1))
    parts, m = [], start.replace(day=1)
    this_month = dt.date.today().replace(day=1)
    while m <= end:
        nxt = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        df = None
        if m < this_month:
            df = fetch(f"{BASE}/{market}/monthly/klines/{sym}/1m/{sym}-1m-{m:%Y-%m}.zip")
        if df is None:                                             # mes en curso (o aún sin fichero mensual): diarios
            days = []
            d = m
            while d < nxt and d <= end:
                x = fetch(f"{BASE}/{market}/daily/klines/{sym}/1m/{sym}-1m-{d:%Y-%m-%d}.zip")
                if x is not None: days.append(x)
                d += dt.timedelta(days=1)
            df = pd.concat(days) if days else None
        if df is not None:
            parts.append(df); print(sym, m, len(df), flush=True)
        m = nxt
    df = pd.concat(parts).sort_index()
    return df[~df.index.duplicated()]

if __name__ == "__main__":
    D.mkdir(exist_ok=True)
    for f, (market, sym, a, b) in SETS.items():
        df = download(market, sym, a, b)
        df.to_pickle(D / f)
        print(f, df.index[0], "->", df.index[-1], len(df))
