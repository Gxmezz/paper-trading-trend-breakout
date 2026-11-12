"""Deriva tras dato macro de EE. UU.: NFP (08:30 NY) y/o decisión FOMC (14:00 NY). Tras `delay` min desde el dato, al cierre
de esa vela se mide el movimiento desde el precio previo al dato (open de la vela que contiene el dato). Si |mov| >= thr×ATR14,
se entra a favor (mode=1, deriva) o en contra (mode=-1, reversión). Salida tras `hold` horas o stop ATR.
Fechas: listas públicas 2020-2025 (verificadas: la vela del dato es de las 1-2 más amplias del día en casi todos los casos).
NFP = primer viernes salvo excepciones documentadas abajo; FOMC sin reuniones de emergencia de 2020 (no fueron a las 14:00)."""
import itertools, numpy as np, pandas as pd

NAME = "Eventos: deriva/reversión tras NFP y FOMC"
TFS = ("M30", "H1")
NY = "America/New_York"

# Excepciones al "primer viernes" (BLS publica el 3.er viernes tras la semana de referencia, y festivos/cierre de gobierno):
# 2020-01-10, 2020-05-08, 2020-07-02 (jue, festivo 3-jul), 2021-01-08, 2021-10-08, 2022-07-08, 2023-03-10, 2023-12-08,
# 2024-03-08, 2025-01-10, 2025-07-03 (jue), oct-2025 sin dato (cierre), 2025-11-20 (jue, dato de sept), 2025-12-16 (mar).
# 2021-04-02 y 2023-04-07 (Viernes Santo): dato publicado con el oro cerrado -> sin vela, se ignora solo.
NFP = """2020-01-10 2020-02-07 2020-03-06 2020-04-03 2020-05-08 2020-06-05 2020-07-02 2020-08-07 2020-09-04 2020-10-02 2020-11-06 2020-12-04
2021-01-08 2021-02-05 2021-03-05 2021-04-02 2021-05-07 2021-06-04 2021-07-02 2021-08-06 2021-09-03 2021-10-08 2021-11-05 2021-12-03
2022-01-07 2022-02-04 2022-03-04 2022-04-01 2022-05-06 2022-06-03 2022-07-08 2022-08-05 2022-09-02 2022-10-07 2022-11-04 2022-12-02
2023-01-06 2023-02-03 2023-03-10 2023-04-07 2023-05-05 2023-06-02 2023-07-07 2023-08-04 2023-09-01 2023-10-06 2023-11-03 2023-12-08
2024-01-05 2024-02-02 2024-03-08 2024-04-05 2024-05-03 2024-06-07 2024-07-05 2024-08-02 2024-09-06 2024-10-04 2024-11-01 2024-12-06
2025-01-10 2025-02-07 2025-03-07 2025-04-04 2025-05-02 2025-06-06 2025-07-03 2025-08-01 2025-09-05 2025-11-20 2025-12-16""".split()
FOMC = """2020-01-29 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16
2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15
2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14
2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13
2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18
2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10""".split()

def events(kind):
    ev = []
    if kind in ("nfp", "both"): ev += [pd.Timestamp(d + " 08:30", tz=NY) for d in NFP]
    if kind in ("fomc", "both"): ev += [pd.Timestamp(d + " 14:00", tz=NY) for d in FOMC]
    return sorted(e.tz_convert("UTC") for e in ev)

def grid():
    return [dict(ev=e, delay=dl, mode=m, thr=t, hold=h, stop_atr=s)
            for e, dl, m, t, h, s in itertools.product(["nfp", "fomc", "both"], [30, 60, 120], [1, -1], [0.0, 0.5, 1.0], [3, 24], [2.0, 3.0])]

def _atr(df, n=14):
    pc = df.Close.shift()
    tr = pd.concat([df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean().to_numpy()

def signals(df, p):
    n = len(df); idx = df.index
    step = idx.to_series().diff().mode().iloc[0]
    close_t = idx + step
    o = df.Open.to_numpy(); c = df.Close.to_numpy(); a = _atr(df)
    L = np.zeros(n, bool); S = np.zeros(n, bool); X = np.zeros(n, bool)
    for e in events(p["ev"]):
        if e < idx[0] or e > idx[-1]: continue
        r = int(close_t.searchsorted(e, side="right"))          # vela que contiene el dato (cierra después del dato)
        if r >= n or idx[r] > e: continue                       # sin vela en el momento del dato (mercado cerrado)
        s = int(close_t.searchsorted(e + pd.Timedelta(minutes=p["delay"]), side="left"))
        if s >= n or s < r or (idx[s] - e) > pd.Timedelta(hours=4): continue
        mv = c[s] - o[r]
        if abs(mv) < p["thr"] * a[s] or mv == 0: continue
        d = np.sign(mv) * p["mode"]
        (L if d > 0 else S)[s] = True
        x = int(close_t.searchsorted(close_t[s] + pd.Timedelta(hours=p["hold"]), side="left"))
        if x < n: X[x] = True
    return dict(L=L, S=S, XL=X, XS=X)
