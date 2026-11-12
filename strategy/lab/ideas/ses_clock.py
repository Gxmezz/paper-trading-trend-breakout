"""Efecto hora del día: entrar a una hora fija de NY (al cierre de la vela H1 que acaba a esa hora) en una dirección fija
y salir tras `hold` horas. Sin indicadores: la ventaja, si existe, es solo del reloj (p. ej. deriva alcista asiática,
debilidad en horario de Londres/NY). El juez elige ventana y dirección con los 2 semestres anteriores. Stop amplio 3 ATR."""
import itertools, numpy as np
from ind import bar_close_ny

NAME = "Sesión: hora del día fija (entrada a hora NY, salida tras N horas)"
TFS = ("H1",)
HOURS = [h for h in range(24) if h not in (17, 18)]     # sin velas que cierren a las 17 ni 18 NY (mercado cerrado 17-18)

def grid():
    return [dict(h=h, hold=k, dir=d, stop_atr=3.0, max_bars=k)
            for h, k, d in itertools.product(HOURS, [1, 2, 4, 8], [1, -1])]

def signals(df, p):
    hc = bar_close_ny(df, 60)
    sig = np.isclose(hc, p["h"])
    z = np.zeros(len(df), bool)
    return dict(L=sig if p["dir"] == 1 else z, S=sig if p["dir"] == -1 else z)
