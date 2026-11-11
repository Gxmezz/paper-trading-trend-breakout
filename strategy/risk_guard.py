"""
Guardián de riesgo: límites DUROS de Cerebro/02-Areas/Reglas de riesgo.md. Lo usan igual el backtest SMC (smc.py),
la cuenta SMC mecánica y la futura cuenta de agentes: ningún agente puede saltárselos ni ampliarlos.

  - Stop obligatorio, al lado correcto del precio.
  - Riesgo por operación: `risk` (2 %) del capital, en onzas enteras. Si 1 oz ya arriesga más de `max_risk`,
    no se opera (nunca se fuerza el tamaño para que quepa).
  - Pérdida diaria máxima `daily_max` (5 %): no se abre ninguna operación cuyo riesgo completo no quepa en lo que
    queda del día.
  - `max_losses` (4) pérdidas en el día paran el día (se cuentan todas las del día: más estricto que "seguidas",
    y es como lo mide el backtest).
  - Ratio beneficio:riesgo mínimo `min_rr` (2:1) sobre el objetivo medio.
  - Apalancamiento máximo 1:50 (si se supera, se reduce el tamaño).
  - Gestión de una operación abierta: el stop solo puede ACERCARSE (reducir riesgo), nunca alejarse.
"""
import math

STEP = 1.0       # oz (0,01 lote de XAUUSD en Vantage)
MAX_LEV = 50.0

def size_entry(eq, day_start, day_losses, px, dist, risk=0.02, max_risk=0.02, daily_max=0.05, max_losses=4,
               targets_r=None, min_rr=2.0, step=STEP, max_lev=MAX_LEV):
    """Tamaño permitido para una entrada. eq/day_start en $ (capital ahora / al empezar el día de negociación),
    px = precio de entrada, dist = distancia al stop en $, targets_r = objetivos en múltiplos de R.
    Devuelve (oz, motivo): oz = 0 si se rechaza, con el motivo."""
    if not (dist > 0) or not math.isfinite(dist): return 0.0, "sin stop válido"
    if max_losses and day_losses >= max_losses: return 0.0, f"día parado: {day_losses} pérdidas hoy"
    if targets_r and min_rr and sum(targets_r) / len(targets_r) < min_rr - 1e-9:
        return 0.0, f"ratio {sum(targets_r) / len(targets_r):.2f}:1 < {min_rr:g}:1"
    q = math.floor(eq * risk / dist / step) * step
    if q < step:
        if step * dist <= eq * max_risk: q = step
        else: return 0.0, f"1 oz arriesgaría {step * dist / eq:.1%} (> {max_risk:.0%})"
    day_loss = (day_start - eq) / day_start
    if day_loss + q * dist / eq > daily_max + 1e-9:
        return 0.0, f"no cabe en el límite diario (hoy {-day_loss:+.1%}, riesgo {q * dist / eq:.1%})"
    if q * px > eq * max_lev:
        q = math.floor(eq * max_lev / px / step) * step
        if q < step: return 0.0, "apalancamiento máximo"
    return float(q), "ok"

def check_stop_change(side, old_stop, new_stop, price):
    """Un cambio de stop solo se acepta si reduce el riesgo (se acerca) y deja el stop al lado correcto del precio."""
    if side == 1:
        if new_stop < old_stop - 1e-9: return False, "el stop de un largo no puede bajar"
        if new_stop >= price: return False, "el stop quedaría por encima del precio"
    else:
        if new_stop > old_stop + 1e-9: return False, "el stop de un corto no puede subir"
        if new_stop <= price: return False, "el stop quedaría por debajo del precio"
    return True, "ok"
