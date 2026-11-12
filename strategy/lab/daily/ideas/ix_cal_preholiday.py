"""Índices · CALENDARIO 2: días previos a festivo de la bolsa de EE. UU. (Ariel 1990, Kim-Park 1994).

Razón: antes de un cierre largo se cierran cortos y coberturas y baja la oferta (sesiones cortas, poco volumen).
Entrada al OPEN de la sesión -pre respecto a la última sesión antes del festivo (pre=1: la propia víspera; pre=2: un día
antes), salida al open tras max_bars sesiones (max_bars=pre: sale a la apertura tras el festivo; pre+1: un día más).
Festivos por REGLAS (calendario NYSE de ix_cal_tom.nyse_holidays; incluye Fin de Año: la víspera es el 31-dic) ->
la condición se conoce de antemano, sin mirar al futuro. ~9 eventos por año.
"""
import itertools, numpy as np, pandas as pd
from ix_cal_tom import trading_calendar

NAME = "Índices: víspera de festivo NYSE"

def grid():
    return [dict(pre=pr, extra=ex, max_bars=pr + ex, stop_atr=k, trend=tr)
            for pr, ex, k, tr in itertools.product([1, 2], [0, 1, 2], [3.0, 5.0], [False, True])]

def flag_pre(df, pre):
    """True en la vela cuya SIGUIENTE sesión (por reglas) está a `pre` sesiones de la víspera de festivo (pre=1: es la víspera)."""
    cal = trading_calendar(); hn = cal.holiday_next.to_numpy(copy=True)
    tgt = np.zeros(len(cal), bool); idx = np.flatnonzero(hn) - (pre - 1); tgt[idx[idx >= 0]] = True
    d = pd.DatetimeIndex(df.date).normalize().values
    j = np.searchsorted(cal.index.values, d, side="right")
    return tgt[j]

def signals(df, p):
    from dind import sma
    L = flag_pre(df, p["pre"])
    if p["trend"]: L &= (df.Close > sma(df.Close, 200)).to_numpy(copy=True)
    return dict(L=L, S=np.zeros(len(df), bool))
