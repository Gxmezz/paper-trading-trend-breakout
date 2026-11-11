"""
Oro diario largo para el laboratorio diario/semanal.

  XAUUSD_mt5_D1.pkl : spot de Vantage (MT5), 2018-03 -> hoy (velas diarias de servidor, NY+7).
  GC_yahoo_D1.pkl   : futuro continuo COMEX (Yahoo GC=F), 2000-08 -> hoy. Tiene saltos de cambio de contrato
                      y el coste de financiación ya metido en el precio; se usa SOLO antes de 2018-03.
  XAU_D1.pkl        : serie unida: GC=F hasta 2018-03-16 (reescalado al spot en la fecha de unión) + MT5 desde entonces.

Uso: python strategy/download_daily.py   (necesita el terminal MT5 abierto para la parte de Vantage)
"""
import datetime as dt
from pathlib import Path
import pandas as pd, requests

D = Path(__file__).parent / "data"

def yahoo(sym="GC=F"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    r = requests.get(url, params=dict(period1=946684800, period2=int(dt.datetime.now().timestamp()), interval="1d"),
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status(); j = r.json()["chart"]["result"][0]; q = j["indicators"]["quote"][0]
    idx = pd.to_datetime(j["timestamp"], unit="s", utc=True).tz_convert("America/New_York").normalize().tz_localize(None)
    df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"]}, index=idx).dropna()
    return df[~df.index.duplicated(keep="last")]

def mt5_daily(sym="XAUUSD"):
    import MetaTrader5 as mt5
    if not mt5.initialize(): raise SystemExit(f"MT5 no disponible: {mt5.last_error()}")
    r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_D1, dt.datetime(2018, 1, 1), dt.datetime.now() + dt.timedelta(days=1)); mt5.shutdown()
    d = pd.DataFrame(r)
    # la vela diaria de servidor empieza a las 00:00 servidor = 17:00 NY del día anterior: se etiqueta con la fecha de cierre
    idx = pd.to_datetime(d.time, unit="s").dt.normalize()
    df = pd.DataFrame({"Open": d.open.values, "High": d.high.values, "Low": d.low.values, "Close": d.close.values,
                       "spread": d.spread.values}, index=pd.DatetimeIndex(idx.values))
    df = df[df.index >= "2018-03-19"]          # antes de esta fecha hay un hueco de 10 años
    return df[df.index.dayofweek < 5]

if __name__ == "__main__":
    g = yahoo(); g.to_pickle(D / "GC_yahoo_D1.pkl"); print("GC=F", g.index[0].date(), "->", g.index[-1].date(), len(g))
    s = mt5_daily(); s.to_pickle(D / "XAUUSD_mt5_D1.pkl"); print("MT5 ", s.index[0].date(), "->", s.index[-1].date(), len(s))
    j = s.index[0]; k = s.Close.iloc[0] / g.Close[:j].iloc[-1]
    old = g[g.index < j][["Open", "High", "Low", "Close"]] * k
    xau = pd.concat([old, s[["Open", "High", "Low", "Close"]]]); xau["src"] = ["GC"] * len(old) + ["MT5"] * len(s)
    xau.to_pickle(D / "XAU_D1.pkl"); print("unida", xau.index[0].date(), "->", xau.index[-1].date(), len(xau), "factor", round(k, 4))
