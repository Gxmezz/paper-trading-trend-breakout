"""Índices · CALENDARIO 3 (fijada ANTES de ver resultados): unión de los dos efectos de flujos -> cambio de mes
(entrada en la sesión -pre del mes) + víspera de festivo (entrada en la víspera), misma salida por tiempo (max_bars).
Más operaciones por año con la misma lógica económica (días de entrada de dinero / falta de oferta). Sin mirar al futuro:
ambos calendarios por reglas (ix_cal_tom). Si ya hay posición abierta, la nueva señal se ignora (lo hace el motor).
"""
import itertools, numpy as np
from ix_cal_tom import next_session
from ix_cal_preholiday import flag_pre

NAME = "Índices: cambio de mes + víspera de festivo"

def grid():
    return [dict(pre=pr, max_bars=mb, stop_atr=k, trend=tr)
            for pr, mb, k, tr in itertools.product([1, 2, 3], [2, 3, 4, 5], [3.0, 5.0], [False, True])]

def signals(df, p):
    from dind import sma
    L = (next_session(df).pos_end.to_numpy(copy=True) == -p["pre"]) | flag_pre(df, 1)
    if p["trend"]: L &= (df.Close > sma(df.Close, 200)).to_numpy(copy=True)
    return dict(L=L, S=np.zeros(len(df), bool))
