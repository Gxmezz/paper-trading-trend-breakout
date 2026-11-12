# Examen reservado 2025-26 · índices (prerregistro)

Escrito el 2026-11-12, ANTES de ejecutar el examen. El examen solo se ejecuta una vez y estos criterios no se cambian.

**Candidatas** (aprueban A-G del juez v3 en 1990-2024 y sobreviven al escéptico, `strategy/lab/audit/ix_audit_mr.py`):
1. `ix_mr_dip` en NDX
2. `ix_mr_dip` en SPX
3. `ix_mr_rsi2` en SPX

**Procedimiento:** `LAB_EXAM=1 python strategy/lab/daily/djudge.py <idea> --asset <NDX|SPX> --exam`. La combinación se elige solo con 2022-2024 (top 1, como en el walk-forward) y se opera de 2025-01-01 al último dato. Mismas reglas de ejecución que en la investigación (señal al cierre, entrada a la apertura siguiente).

**Aprueba el examen si cumple las dos:**
1. Rentabilidad > 0 con coste base (0,01 % por lado) y con coste de estrés (0,02 % por lado).
2. MAR (rentabilidad anual / |caída máxima|) ≥ MAR de comprar y mantener el índice (con dividendos) en el mismo periodo.

**Advertencias fijadas de antemano:**
- Son 22 meses: un examen corto. Aprobarlo no demuestra la ventaja; suspenderlo la pone en duda seria.
- Las tres candidatas son el mismo fenómeno (reversión corta), no tres confirmaciones independientes.
- La ventaja depende de entrar justo tras el cierre de la señal (el escéptico: con un día de retraso desaparece).

## Resultado (ejecutado el 2026-11-12, una sola vez)

| Candidata | Parámetros elegidos con 2022-24 | Rentabilidad base / estrés | MAR | MAR comprar y mantener | Veredicto |
|---|---|---|---|---|---|
| ix_mr_dip NDX | 3 cierres bajistas, sin media, salida SMA5, stop 4 ATR, 10 días | +0,1 % / +0,0 % (21 ops) | 0,04 | 0,81 | **Suspende** (1 y 2) |
| ix_mr_dip SPX | 4 cierres bajistas, sin media, salida RSI(2) > 70, stop 4 ATR, 10 días | +1,1 % / +1,0 % (12 ops) | 0,57 | 0,93 | **Suspende** (2) |
| ix_mr_rsi2 SPX | RSI(2) < 25 sobre SMA100, salida RSI(2) > 70, stop 3 ATR, 10 días | +3,4 % / +3,2 % (33 ops) | 0,99 (estrés 0,90) | 0,93 | **Aprueba por poco** |

Lectura: 2025-26 fue muy alcista (comprar y mantener con MAR 0,8-0,9), un examen exigente para estrategias que están poco tiempo en el mercado. Solo RSI(2) en el S&P 500 aprueba, y por un margen pequeño (con coste de estrés quedaría por debajo). Las tres son el mismo fenómeno: la evidencia del examen es mixta.
