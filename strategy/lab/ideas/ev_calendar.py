"""Calendario: día de la semana y cambio de mes. Día de trading = fecha NY de la sesión (18:00 NY del día anterior a 17:00).
Conjuntos de días: lun..vie, TOM (último día hábil del mes + 2 primeros), FOM (5 primeros días hábiles), MID (días 10-20).
Los días hábiles salen del calendario (lun-vie), no de los datos: no mira al futuro. Entrada en dirección fija al cierre de
la vela que acaba a la hora `ent` NY (19 = vela tras la apertura de la sesión, 3 = Londres, 8 = antes de NY); salida al cierre
de la vela que acaba a `ex` NY del mismo día de trading, o stop ATR."""
import itertools, numpy as np, pandas as pd

NAME = "Calendario: día de la semana / cambio de mes, dirección fija"
TFS = ("M30", "H1")
SETS = ["mon", "tue", "wed", "thu", "fri", "tom", "fom", "mid"]

def grid():
    return [dict(days=d, dir=s, ent=e, ex=x, stop_atr=k)
            for d, s, e, x, k in itertools.product(SETS, [1, -1], [19, 3, 8], [12, 16], [2.0, 4.0])]

def _days(td, which):
    td = pd.DatetimeIndex(td)
    wd = td.weekday
    if which in SETS[:5]: return wd == SETS.index(which)
    bd = pd.offsets.BDay()
    first_k = np.array([len(pd.bdate_range(t.replace(day=1), t)) for t in td]) if which in ("tom", "fom") else None
    if which == "fom": return (first_k <= 5) & (wd < 5)
    if which == "tom":
        last = np.array([(t + bd).month != t.month for t in td])
        return ((first_k <= 2) | last) & (wd < 5)
    return (td.day >= 10) & (td.day <= 20) & (wd < 5)

def signals(df, p):
    step = df.index.to_series().diff().mode().iloc[0]
    t = (df.index + step).tz_convert("America/New_York")           # hora NY al CIERRE de la vela
    hc = t.hour.to_numpy() + t.minute.to_numpy() / 60
    td = (t.tz_localize(None) + pd.Timedelta(hours=6)).normalize()  # día de trading (sesión desde las 18:00 NY)
    u = pd.DatetimeIndex(td.unique()); ok = dict(zip(u, _days(u, p["days"])))
    inset = np.array([ok[x] for x in td])
    sig = inset & np.isclose(hc, p["ent"])
    X = np.isclose(hc, p["ex"]) | np.isclose(hc, 16.5)             # 16:30 NY: cierre de seguridad antes del cierre diario
    z = np.zeros(len(df), bool)
    return dict(L=sig if p["dir"] == 1 else z, S=sig if p["dir"] == -1 else z, XL=X, XS=X)
