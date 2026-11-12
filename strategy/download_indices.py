"""
Datos diarios para el laboratorio de índices (Nasdaq 100 y S&P 500).

  NDX_D1.pkl / SPX_D1.pkl : índice de contado de Yahoo (^NDX desde 1985-10, ^GSPC desde 1985-01). Es el precio que
                            replican los CFD de contado de Vantage (NAS100.r / SP500.r). No incluye dividendos.
  VIX_D1.pkl              : índice de volatilidad VIX (Yahoo ^VIX, desde 1990).
  RATE_D1.pkl             : tipo de la letra del Tesoro a 3 meses (FRED DTB3; si no responde, Yahoo ^IRX), en %, para el swap de cada época.
  NAS100_mt5_D1.pkl / SP500_mt5_D1.pkl : CFD de Vantage (MT5, desde 2018) solo para validar (necesita MT5 abierto).

Uso: python strategy/download_indices.py
"""
import datetime as dt, io
from pathlib import Path
import pandas as pd, requests

D = Path(__file__).parent / "data"
UA = {"User-Agent": "Mozilla/5.0"}

def yahoo(sym, start=473385600):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", headers=UA, timeout=60,
                     params=dict(period1=start, period2=int(dt.datetime.now().timestamp()), interval="1d"))
    r.raise_for_status(); j = r.json()["chart"]["result"][0]; q = j["indicators"]["quote"][0]
    idx = pd.to_datetime(j["timestamp"], unit="s", utc=True).tz_convert("America/New_York").normalize().tz_localize(None)
    df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"]}, index=idx).dropna()
    return df[~df.index.duplicated(keep="last")]

def fred(series="DTB3"):
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series}, headers=UA, timeout=25)
    r.raise_for_status()
    x = pd.read_csv(io.StringIO(r.text)); x.columns = ["date", "rate"]
    x["date"] = pd.to_datetime(x["date"]); x["rate"] = pd.to_numeric(x["rate"], errors="coerce")
    return x.dropna().set_index("date")["rate"]

def mt5_daily(sym):
    import MetaTrader5 as mt5
    if not mt5.initialize(): return None
    r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, dt.datetime(2018, 1, 1), dt.datetime.now() + dt.timedelta(days=1)); mt5.shutdown()
    if r is None or not len(r): return None
    d = pd.DataFrame(r); idx = pd.to_datetime(d.time, unit="s").dt.normalize()
    df = pd.DataFrame({"Open": d.open.values, "High": d.high.values, "Low": d.low.values, "Close": d.close.values},
                      index=pd.DatetimeIndex(idx.values))
    return df[df.index.day_of_week < 5]

if __name__ == "__main__":
    for name, sym in (("NDX", "^NDX"), ("SPX", "^GSPC"), ("VIX", "^VIX")):
        df = yahoo(sym); df.to_pickle(D / f"{name}_D1.pkl"); print(name, df.index[0].date(), "->", df.index[-1].date(), len(df))
    try: rt = fred()
    except Exception as ex:                       # FRED a veces no responde: mismo dato en Yahoo (^IRX, letra a 13 semanas)
        print("FRED no disponible, se usa ^IRX de Yahoo:", type(ex).__name__); rt = yahoo("^IRX", start=0).Close
    rt.to_pickle(D / "RATE_D1.pkl"); print("tipo 3 meses", rt.index[0].date(), "->", rt.index[-1].date(), "último", rt.iloc[-1])
    for name, sym in (("NAS100", "NAS100.r"), ("SP500", "SP500.r")):
        try:
            m = mt5_daily(sym)
            if m is not None: m.to_pickle(D / f"{name}_mt5_D1.pkl"); print(name, "MT5", m.index[0].date(), "->", m.index[-1].date(), len(m))
        except Exception as ex:
            print(name, "MT5 no disponible:", ex)
