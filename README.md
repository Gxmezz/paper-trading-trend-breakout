# Paper trading · Oro (Vantage) · 1000 € por estrategia

Simulación con **dinero ficticio** de estrategias para el oro (XAUUSD en Vantage), una por temporalidad,
cada una con su propia cuenta de 1000 €. Se ejecuta sola en GitHub Actions (el motor corre cada minuto) y publica el
dashboard en GitHub Pages cada ~7 minutos.

**Dashboard:** https://gxmezz.github.io/paper-trading-trend-breakout/

## Estrategias (máximo 3 indicadores cada una)

| Cuenta | Familia | Reglas | Riesgo |
|---|---|---|---|
| Oro H1 | Ruptura | EMA 200 + ruptura Donchian 10 + stop ATR×2 con trailing · largos y cortos | 1 % |
| Oro M30 | Ruptura con filtro H1 | EMA 200 de H1 + ruptura Donchian 60 + ATR×3 trailing · hasta 3 unidades · largos y cortos · sin entradas en noticias de EE. UU. (08:00–08:45 NY y FOMC 13:30–15:15 NY) | 1 %/unidad |
| Oro M30 + Sniper (prueba) | Igual que Oro M30 | + solo a favor de SMA 9 > SMA 21 en H1 (indicador QNA Sniper de TradingView). En backtest mejora la prueba (+16,6 %/mes, −12 %), pero empeora PAXG; se corre aparte para comprobarlo en vivo (`strategy/sniper.py`) | 1 %/unidad |
| Oro M15 | Pullback | EMA 200 de H1 (tendencia) + RSI(2) < 20 para comprar, sale con RSI(2) > 80 · stop ATR×2 · entradas 07–20 UTC | 1 % |
| Oro M15 · SMC mecánica (control) | SMC del curso | `strategy/smc.py` v2, la mejor combinación de entrenamiento: sesgo de estructura H4 + EMA 200 H1 · Order Block con FVG tras ruptura · confirmación por cierre · stop tras el OB + 0,2 ATR · parciales 2R/3R/4R con break even · killzones · sin entradas en noticias. Repite cada minuto el backtest exacto sobre las velas M15 guardadas. Es la cuenta de control del futuro equipo de agentes (en backtest fuera de muestra perdía −2 %/mes) | 2 % (−5 % diario, 4 pérdidas paran el día) |

### Experimento SMC: agentes frente a mecánica

`strategy/risk_guard.py` aplica las reglas de riesgo como límites duros (2 % por operación en onzas enteras, sin forzar el tamaño; −5 % diario sin abrir si el riesgo no cabe; 4 pérdidas en el día paran el día; ratio ≥ 2:1; el stop de una operación abierta solo puede acercarse). Lo usan el backtest SMC, la cuenta SMC mecánica y la futura cuenta de agentes, así que las reglas son idénticas. Pruebas: `python strategy/test_risk_guard.py`.

Cada setup SMC confirmado queda registrado (vela, lado, zona, stop y decisión: operado, descartado por qué filtro o rechazado por el guardián) en el estado publicado y en la sección *Experimento SMC* del dashboard, junto con la comparativa de las dos cuentas y el progreso hacia la evaluación (100 operaciones o 3 meses).

**Fase 2 (cuenta *SMC agentes*):** `agents.py` llama a Claude Opus 5.5 cada vez que el detector confirma un setup SMC (también los que la gemela descarta por sus filtros). El agente decide si entra, el stop y hasta 3 objetivos, con break even opcional; su respuesta tiene formato JSON fijo y la valida el guardián de riesgo. Cada decisión queda registrada con su motivo y su coste en el dashboard. Tope de gasto: variable del repositorio `PT_AGENT_DAILY_USD` (2 $/día por defecto); `PT_AGENTS=off` desactiva el agente. La clave es el secreto `ANTHROPIC_API_KEY`; el workflow *Prueba de la clave de la API* la comprueba con una llamada mínima.

La cuenta M5 se retiró el 11/11/2026 (no se pudo validar con datos reales: Vantage no guarda M5 antiguo); su estado queda archivado en `state.json`.

M1 se descartó: ninguna de las 1632 combinaciones probadas fue rentable en los dos periodos de entrenamiento.

### Cómo se eligieron

`strategy/vantage_opt.py` prueba 1632 combinaciones por temporalidad (rupturas, rupturas con filtro de H1 y
pullbacks con RSI) con las condiciones de una cuenta de 1000 € en Vantage. Se seleccionan con dos periodos de
entrenamiento (XAUUSDT dic-2025–jun-2026 y PAXG ene–nov 2025) y se comprueban en un periodo que no interviene
en la selección (XAUUSDT jul–oct 2026). Rentabilidad mensual media en cada periodo:

| Cuenta | Entreno XAU | Entreno PAXG | Prueba jul–oct 2026 |
|---|---|---|---|
| H1 | +6,0 %/mes · caída −20 % | +4,8 %/mes · −18 % | +8,6 %/mes · −15 % |
| M30 | +13,9 %/mes · −29 % | +9,7 %/mes · −23 % | +7,9 %/mes · −18 % |
| M15 | +3,7 %/mes · −12 % | +3,7 %/mes · −10 % | +3,8 %/mes · −8 % |
| M5 | +7,2 %/mes · −8 % | +4,7 %/mes · −17 % | +6,3 %/mes · −7 % |

Son simulaciones de un periodo corto (≈2 años de datos de 1 minuto) con un oro muy volátil; no esperes que se
repitan. El paper trading es precisamente la prueba con datos que nadie ha visto.

### Comparación con la estrategia SMC (10/11/2026)

`strategy/smc.py` mecaniza una estrategia Smart Money Concepts de un curso en vídeo:
- **Sesgo**: estructura de H1 (BOS/CHoCH).
- **Zonas**: Order Blocks válidos (fuerza + ruptura de estructura + FVG), FVG y zona OTE de Fibonacci.
- **Entrada**: solo tras una reacción en la zona.
- **Gestión**: stop tras el extremo + margen ATR, 3 parciales en R con break even, filtro de noticias.
- **Riesgo**: 2 % por operación, −5 % diario, 4 pérdidas seguidas paran el día.

`strategy/smc_opt.py` probó 5184 combinaciones por temporalidad con la misma metodología. `strategy/combine.py` compara las formas de juntarla con la estrategia actual. Rentabilidad mensual media y caída máxima:

| Opción | Entreno XAU | Entreno PAXG | Prueba jul–nov 2026 |
|---|---|---|---|
| **B · Actual (4 cuentas)** | +8,0 % · −11 % | +6,0 % · −9 % | **+6,6 % · −5,5 %** |
| A · SMC M15 sola | +2,9 % · −7 % | +4,1 % · −7 % | −1,2 % · −8 % |
| A · SMC M5 sola | +3,9 % · −10 % | +4,6 % · −9 % | −1,8 % · −21 % |
| C · SMC + EMA 200 H1 | ≈0 % | +5 % | −0,8 % |
| D · Cartera actual + SMC (6 cuentas) | +6,6 % · −7 % | +5,5 % · −6,5 % | +4,1 % · −3,9 % |
| E · Actual filtrada por estructura H1 | +8,0 % · −10 % | +5,7 % · −9 % | +2,9 % · −5,6 % |
| B + filtro de noticias | +8,1 % · −10 % | +5,5 % · −9 % | +7,4 % · −6 % |

Conclusiones:
- La SMC es rentable en los dos periodos de entrenamiento, pero **pierde en el periodo de prueba**: no hay ventaja demostrada.
- Juntarla con la estrategia actual reduce la caída máxima, pero también la rentabilidad fuera de muestra.
- Filtro de noticias: con el mismo criterio de selección (el peor de los dos periodos de entrenamiento) solo mejora **M30**: puntuación 0,49 → 0,59. En la prueba pasa de +5,8 % a +9,5 %/mes con la misma caída. En H1, M15 y M5 empeora el peor periodo de entrenamiento, así que no se aplica.
- **Decisión**: se mantienen las 4 cuentas actuales, M30 con filtro de noticias. La SMC no entra en el paper trading.
- Las 4 cuentas actuales siguen siendo rentables en backtest también con datos posteriores a su elección (10/10–09/11/2026: H1 +9 %, M30 +24 %, M15 +2 %, M5 +3,5 %).

Para repetirlo: `python strategy/download_data.py`, luego `python strategy/smc_opt.py M5,M15,M30` y `python strategy/combine.py`.

**SMC v2 (10/11/2026).** `strategy/smc_opt2.py` añade barrida de liquidez, premium/descuento, killzones, TP en liquidez de H1, trailing, sesgo H4, alineación de estructura y filtros EMA 200 / Sniper (36 864 combinaciones por temporalidad):
- Mejor puntuación de entrenamiento: 0,40 (M15) y 0,34 (M5), frente a 0,66–0,72 de la cartera actual.
- Prueba jul–nov 2026: 37 de los 40 mejores pierden. El mejor gana +0,9 %/mes con una caída de −8 %; los que usan el filtro Sniper pierden entre −5 y −8 %/mes.
- **Decisión**: la SMC sigue fuera del paper trading. El examen final (PAXG jul–dic 2024) queda sin usar para una futura estrategia candidata.

## Condiciones simuladas

- Cuenta en EUR: el resultado en USD se convierte al EURUSD del momento.
- Lotes como el XAUUSD estándar de Vantage: 0,01 lote = 1 oz, así que las posiciones van en onzas enteras.
  Con 1000 € el lote mínimo suele arriesgar más del 1 % en H1/M30; si 1 oz arriesga más del 4 % de la
  cuenta, la señal se descarta.
- Costes 0,20 $/oz por lado (Raw ECN: medio spread + comisión de 3 $/lote/lado + deslizamiento). Swap de largos
  6 %/año (triple el miércoles), cortos sin coste. Apalancamiento máximo 1:50.
- Horario del oro en hora de Nueva York (se ajusta solo al horario de verano): cerrado de viernes 17:00 a domingo 18:00 y en la pausa diaria 17:00–18:00 (en Madrid, normalmente de 23:00 a 00:00).
- Precio: perpetuo XAU_USDT de Gate.io corregido cada minuto al oro spot. La diferencia Gate − spot se mide con las cotizaciones públicas de Swissquote (coinciden con VANTAGE:XAUUSD al céntimo) y se resta a todas las velas; el dashboard muestra la corrección aplicada.
- Cada ejecución repite minuto a minuto lo ocurrido desde la anterior, así que los retrasos de GitHub no
  cambian el resultado. El motor reproduce exactamente el backtest (mismas operaciones y resultado).
- Funcionamiento 24/7: cada ejecución del workflow corre el motor cada minuto durante ~5 h 40 min y, al terminar,
  lanza la siguiente (no depende del cron de GitHub, que puede tardar horas; el cron queda como respaldo).
- El dashboard y el estado se publican en la rama `gh-pages` (un solo commit que se sobrescribe).

## Uso

- Si la cadena se detiene: **Actions → Paper trading → Run workflow**.
- Pararlo: **Actions → Paper trading → ··· → Disable workflow** y cancela la ejecución en curso.
- Empezar de cero: borra la rama `gh-pages` y relanza el workflow.
- En local: `pip install requests && python paper_trader.py` (bucle cada 20 s, dashboard en `dashboard.html`).

`strategy/gold_vantage.pine` es el script de TradingView con estas estrategias (elige la temporalidad del gráfico y aplica la suya; úsalo en `VANTAGE:XAUUSD`). `strategy/` también contiene el optimizador, los backtests y el script anterior (`trend_breakout.pine`).

> Simulación educativa. Resultados pasados no garantizan resultados futuros. No es asesoramiento financiero.
