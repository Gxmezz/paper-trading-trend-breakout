"""AJUSTE 1 (post hoc) de ev_macro: solo la hipótesis de DERIVA (mode=1) tras NFP+FOMC juntos, con salidas más largas.
Mismas fechas y misma señal que ev_macro; se registra con --posthoc porque el ajuste se hizo viendo los semestres de ev_macro."""
import itertools
from ev_macro import signals, events, NFP, FOMC  # noqa: F401

NAME = "Eventos: deriva tras NFP+FOMC (ajuste post hoc de ev_macro)"
TFS = ("M30", "H1")

def grid():
    return [dict(ev="both", delay=dl, mode=1, thr=t, hold=h, stop_atr=s)
            for dl, t, h, s in itertools.product([30, 60, 120], [0.0, 0.5, 1.0], [12, 24, 48], [2.0, 3.0, 4.0])]
