"""
Descarga velas M30 del MT5 de Vantage (terminal abierto, solo lectura) y las guarda en strategy/data/<SIMBOLO>_mt5_M30.pkl
con la hora convertida a UTC (hora de servidor = Nueva York + 7 h).

Vantage solo guarda histórico antiguo en M30 y superiores (M1/M5/M15 solo recientes).
Uso: python strategy/download_mt5.py XAUUSD,XAGUSD,EURUSD
"""
import sys, datetime as dt
from pathlib import Path
import MetaTrader5 as mt5, pandas as pd

D = Path(__file__).parent / "data"

def download(sym, y0=2020, y1=2026):
    mt5.symbol_select(sym, True)
    parts = []
    for y in range(y0, y1 + 1):
        r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M30, dt.datetime(y, 1, 1), dt.datetime(y, 12, 31, 23, 59))
        if r is not None and len(r): parts.append(pd.DataFrame(r))
    if not parts: return None
    d = pd.concat(parts).drop_duplicates("time")
    t = pd.to_datetime(d.time, unit="s") - pd.Timedelta(hours=7)
    utc = t.dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    df = pd.DataFrame({"Open": d.open.values, "High": d.high.values, "Low": d.low.values, "Close": d.close.values,
                       "spread": d.spread.values}, index=pd.DatetimeIndex(utc.values))
    df = df[df.index.notna()].sort_index()
    if df.index.tz is None: df.index = df.index.tz_localize("UTC")
    name = sym.split(".")[0]
    df.to_pickle(D / f"{name}_mt5_M30.pkl")
    return df

if __name__ == "__main__":
    if not mt5.initialize(): sys.exit(f"MT5 no disponible: {mt5.last_error()}")
    for s in sys.argv[1].split(","):
        df = download(s)
        print(s, "sin datos" if df is None else f"{df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d}  {len(df)} velas", flush=True)
    mt5.shutdown()
