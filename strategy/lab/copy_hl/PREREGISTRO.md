# Examen prerregistrado: copiar carteras públicas de Hyperliquid

Fijado el 2026-11-16, **antes de ver ningún resultado de copia**. No se cambia después.

## Pregunta
¿Elegir carteras de Hyperliquid con su pasado y copiarlas (con retraso y costes)
da ventaja? ¿Y un detector de cambio de estilo que las sustituya mejora el resultado?

## Datos
- Ranking público completo (47 897 carteras; el 53 % pierde y el 46 % está casi a cero,
  así que incluye las que fracasaron).
- Muestra **al azar** (semilla 42) de 1000 carteras con volumen total de 10 M a 5 000 M $.
  No se filtra por beneficio. (Iban a ser 1500; se bajó a 1000 por el límite de la API,
  ~3 h de descarga, antes de ver resultados. Son las 1000 primeras del mismo sorteo.)
- Por cartera: operaciones (últimas 10 000) y curva de beneficio (~semanal).
- Precios: velas de 1 min de Binance futuros. Funding: horario de Hyperliquid.

## Simulación de la copia
- Se copian solo las monedas con velas de Binance (lista fija = las que suman el 90 %
  del volumen de la muestra y existen en Binance).
- Retraso: **60 s** desde cada operación de la cartera; precio = apertura del minuto
  siguiente a ese retraso.
- Costes: comisión taker 0,045 % + deslizamiento 0,03 % por lado; funding horario real.
- Tamaño: capital repartido a partes iguales entre las k carteras. La posición copiada es
  la de la cartera × (capital asignado / valor de su cuenta en la última medición
  conocida). Apalancamiento máximo de la copia: 3× por cartera (si lo supera, se reduce).
- Reequilibrio: el día 1 de cada mes se eligen las k = 10 carteras; las posiciones de
  las que salen se cierran.

## Filtro de "copiable" (con datos hasta la fecha de elección)
- ≥ 90 días de historial, operó en los últimos 30 días.
- ≤ 40 operaciones/día de media; mediana de duración de posición ≥ 30 min.
- ≥ 70 % de su volumen en monedas copiables.

## Estrategias
- **S0 Azar**: k carteras copiables al azar (500 sorteos para la distribución).
- **S1 Top rentabilidad**: mayor rentabilidad de los últimos 90 días.
- **S2 Calidad**: mejor ratio rentabilidad/caída y regularidad (Sharpe semanal) de los
  últimos 180 días, con caída máxima ≤ 40 %.
- **S3 Calidad + detector de cambio de estilo**: S2 y, cada semana, se sustituye una
  cartera si (a) su copia cae más de X % desde que se copia, o (b) su estilo se sale de
  su historial: apalancamiento, operaciones/día o duración media fuera de
  [p10/f, p90·f] de sus últimos 180 días, o cambia de monedas principales.
- **Referencia**: comprar y mantener BTC.

Los parámetros (X, f, pesos de S2) se eligen **solo** con el periodo de diseño, con una
rejilla pequeña (≤ 30 combinaciones), y se congelan antes del examen.

Concreción (añadida el mismo día, antes de ver resultados):
- Las métricas de rentabilidad y caída para elegir (S1, S2) se calculan sobre la **copia
  simulada** de cada cartera (con retraso y costes) en la ventana pasada, no sobre su
  propia curva.
- S2: puntuación = w · rango(Sharpe semanal) + (1 − w) · rango(rentabilidad / caída).
- Rejilla: X ∈ {15, 25, 35} %, f ∈ {1, 1,5, 2}, w ∈ {0, 0,5, 1} → 27 combinaciones.
  En S2 solo cuenta w. Se elige la combinación de **mayor MAR en el periodo de diseño**.
- Revisiones del detector: cada lunes 00:00 UTC. Una cartera sustituida entra por la
  siguiente del ranking de S2 en ese momento que no esté ya en la cartera.
- Si una cartera sigue de un mes al siguiente, no se cierra ni se paga coste de salida.
- Corrección de un fallo (antes del examen): el apalancamiento semanal salía 0 cuando no
  había valor de cuenta conocido y el detector lo contaba como cambio de estilo. Ahora es
  un dato ausente. El diseño se repitió con la corrección.

## Periodos
- Diseño: 2025-03-01 → 2025-12-31.
- **Examen: 2026-01-01 → 2026-11-15**, se usa una sola vez.

## Criterios de aprobado (examen, todos)
- **A**: S3 gana dinero después de costes.
- **B**: S3 supera al 90 % de los sorteos de S0 (azar).
- **C**: S3 rinde más que S1 y que S2 (el detector aporta).
- **D**: caída máxima ≤ 30 % y MAR (rentabilidad anual / caída máxima) mayor que el
  de comprar y mantener BTC.

Si aprueba S2 pero no S3, se aprueba "copiar sin detector". Si no aprueba ninguna, se
descarta y no se pasa a paper trading.

## Limitaciones conocidas
- La API solo da las 10 000 últimas operaciones: las carteras muy activas tienen poco
  historial (y además no son copiables).
- La muestra se toma del ranking actual: una cartera que borró su historial no estaría
  (es raro en Hyperliquid, que no deja borrarlo).
- Se simula con precios de Binance, no del libro de Hyperliquid.
