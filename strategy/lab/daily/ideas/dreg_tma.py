"""Régimen por media de largo plazo (Faber): largo mientras el oro esté sobre su media de N meses / N semanas.
check='P': se decide con el cierre del periodo anterior (mensual/semanal); 'D': cierre diario vs la media del periodo
anterior. Opcional: corto en el régimen contrario. Stop = pct del precio (amplio). max_bars=21 renueva la posición
cada ~mes (para que haya operaciones cerradas; coste despreciable)."""
import itertools, numpy as np
from dind import sma, period_closes, align_prev

NAME = "Régimen: precio sobre media de N meses/semanas"

def grid():
    lens = [("M", 6), ("M", 8), ("M", 10), ("M", 12), ("W", 26), ("W", 40), ("W", 52)]
    return [dict(freq=f, n=n, check=ch, shorts=sh, stop_pct=sp, max_bars=21, stop_atr=1.0)
            for (f, n), ch, sh, sp in itertools.product(lens, ["P", "D"], [False, True], [0.05, 0.10, 0.20])]

def regime(df, p):
    pc = period_closes(df, p["freq"]); m = sma(pc, p["n"])
    if p["check"] == "P":
        up = align_prev(df, p["freq"], (pc > m).astype(float).where(m.notna()))
        dn = align_prev(df, p["freq"], (pc < m).astype(float).where(m.notna()))
        return (up == 1).to_numpy(copy=True), (dn == 1).to_numpy(copy=True)
    mm = align_prev(df, p["freq"], m)
    return (df.Close > mm).to_numpy(copy=True), (df.Close < mm).to_numpy(copy=True)

def signals(df, p):
    up, dn = regime(df, p)
    S = dn if p["shorts"] else np.zeros(len(df), bool)
    return dict(L=up, S=S, XL=~up, XS=~dn, stop=(p["stop_pct"] * df.Close).to_numpy(copy=True))
