"""Índices · CALENDARIO 1: cambio de mes (turn of month) contado en SESIONES HÁBILES de la bolsa de EE. UU.

Razón económica: nóminas, aportaciones automáticas a planes de pensiones/fondos y rebalanceos se concentran en los
últimos y primeros días hábiles del mes (Ariel 1987, Lakonishok-Smidt 1988, McConnell-Xu 2008).
Entrada al OPEN de la sesión hábil número -pre del mes (-1 = última), salida al open tras `max_bars` sesiones
(pre sesiones del mes que acaba + post del nuevo). Señal al cierre de la sesión anterior: la posición en el mes de la
SIGUIENTE sesión se calcula con un calendario NYSE construido por REGLAS (no con los huecos de df.date) -> sin mirar al futuro.

Calendario NYSE por reglas (nyse_holidays): Año Nuevo (domingo -> lunes; sábado -> no se observa), Martin Luther King
(3.er lunes de enero, desde 1998), Presidentes (3.er lunes de febrero), Viernes Santo, Memorial Day (último lunes de
mayo), Juneteenth (19-jun desde 2022), 4 de julio, Labor Day (1.er lunes de septiembre), Acción de Gracias (4.º jueves
de noviembre) y Navidad; los fijos sábado -> viernes, domingo -> lunes. NO incluye cierres imprevistos (huracanes,
11-S, funerales de Estado): son pocos días y solo desplazan una sesión.
"""
import itertools, numpy as np, pandas as pd
from dateutil.easter import easter

NAME = "Índices: cambio de mes en sesiones hábiles NYSE"

def _nth_weekday(y, m, wd, n):          # n-ésimo día de la semana wd (0 = lunes) del mes; n = -1 último
    if n > 0:
        d = pd.Timestamp(y, m, 1); d += pd.Timedelta(days=(wd - d.weekday()) % 7); return d + pd.Timedelta(weeks=n - 1)
    d = pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0); return d - pd.Timedelta(days=(d.weekday() - wd) % 7)

def _obs(d, sat_to_fri=True):
    if d.weekday() == 5: return d - pd.Timedelta(days=1) if sat_to_fri else None
    if d.weekday() == 6: return d + pd.Timedelta(days=1)
    return d

def nyse_holidays(y0=1980, y1=2030):
    out = []
    for y in range(y0, y1 + 1):
        out.append(_obs(pd.Timestamp(y, 1, 1), sat_to_fri=False))
        if y >= 1998: out.append(_nth_weekday(y, 1, 0, 3))
        out.append(_nth_weekday(y, 2, 0, 3))
        out.append(pd.Timestamp(easter(y)) - pd.Timedelta(days=2))
        out.append(_nth_weekday(y, 5, 0, -1))
        if y >= 2022: out.append(_obs(pd.Timestamp(y, 6, 19)))
        out.append(_obs(pd.Timestamp(y, 7, 4)))
        out.append(_nth_weekday(y, 9, 0, 1))
        out.append(_nth_weekday(y, 11, 3, 4))
        out.append(_obs(pd.Timestamp(y, 12, 25)))
    return pd.DatetimeIndex(sorted(d for d in out if d is not None))

_CAL = None
def trading_calendar():
    """Sesiones NYSE por reglas, con su posición en el mes: pos_end (-1 = última sesión del mes) y pos_start (1 = primera)."""
    global _CAL
    if _CAL is None:
        hol = nyse_holidays()
        days = pd.bdate_range("1980-01-01", "2030-12-31"); days = days[~days.isin(hol)]
        per = days.to_period("M"); g = pd.Series(1, index=days).groupby(per)
        ps = g.cumsum().values; n = g.transform("size").values
        gap = np.r_[np.diff(days.values).astype("timedelta64[D]").astype(int), 1]
        _CAL = pd.DataFrame(dict(pos_start=ps, pos_end=ps - n - 1,
                                 holiday_next=(gap > 1) & ~((days.weekday == 4) & (gap == 3))), index=days)
    return _CAL

def next_session(df):
    """Para cada vela, la fila del calendario de la SIGUIENTE sesión hábil por reglas (conocida al cierre de la vela)."""
    cal = trading_calendar(); d = pd.DatetimeIndex(df.date).normalize()
    j = np.searchsorted(cal.index.values, d.values, side="right")
    return cal.iloc[j].reset_index(drop=True)

def grid():
    return [dict(pre=pr, post=po, max_bars=pr + po, stop_atr=k, trend=tr)
            for pr, po, k, tr in itertools.product([1, 2, 3], [1, 2, 3, 4], [3.0, 5.0], [False, True])]

def signals(df, p):
    from dind import sma
    nx = next_session(df)
    L = nx.pos_end.to_numpy(copy=True) == -p["pre"]            # mañana es la sesión -pre del mes -> entrar a su apertura
    if p["trend"]: L &= (df.Close > sma(df.Close, 200)).to_numpy(copy=True)
    return dict(L=L, S=np.zeros(len(df), bool))
