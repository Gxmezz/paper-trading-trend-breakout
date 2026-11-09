# Paper trading · Oro (Vantage) · 1000 € por estrategia

Simulación con **dinero ficticio** de estrategias para el oro (XAUUSD en Vantage), una por temporalidad,
cada una con su propia cuenta de 1000 €. Se ejecuta sola en GitHub Actions cada ~10 minutos y publica el
dashboard en GitHub Pages.

**Dashboard:** https://gxmezz.github.io/paper-trading-trend-breakout/

## Estrategias (máximo 3 indicadores cada una)

| Cuenta | Familia | Reglas | Riesgo |
|---|---|---|---|
| Oro H1 | Ruptura | EMA 200 + ruptura Donchian 10 + stop ATR×2 con trailing · largos y cortos | 1 % |
| Oro M30 | Ruptura con filtro H1 | EMA 200 de H1 + ruptura Donchian 60 + ATR×3 trailing · hasta 3 unidades · largos y cortos | 1 %/unidad |
| Oro M15 | Pullback | EMA 200 de H1 (tendencia) + RSI(2) < 20 para comprar, sale con RSI(2) > 80 · stop ATR×2 · entradas 07–20 UTC | 1 % |
| Oro M5 | Pullback | EMA 200 de H1 + RSI(3) < 5 para comprar, sale con RSI(3) > 80 · stop ATR×2 · entradas 07–20 UTC | 2 % |

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

## Condiciones simuladas

- Cuenta en EUR: el resultado en USD se convierte al EURUSD del momento.
- Lotes como el XAUUSD estándar de Vantage: 0,01 lote = 1 oz, así que las posiciones van en onzas enteras.
  Con 1000 € el lote mínimo suele arriesgar más del 1 % en H1/M30; si 1 oz arriesga más del 3 % de la
  cuenta, la señal se descarta.
- Costes 0,20 $/oz por lado (Raw ECN: medio spread + comisión de 3 $/lote/lado + deslizamiento). Swap de largos
  6 %/año (triple el miércoles), cortos sin coste. Apalancamiento máximo 1:50.
- Horario del oro: cerrado de viernes 21:00 a domingo 22:00 UTC y en la pausa diaria 21:00–22:00 UTC.
- Precio: perpetuo XAU_USDT de Gate.io (cotiza como el oro spot y es accesible desde los servidores de GitHub).
- Cada ejecución repite minuto a minuto lo ocurrido desde la anterior, así que los retrasos de GitHub no
  cambian el resultado. El motor reproduce exactamente el backtest (mismas operaciones y resultado).
- El estado se guarda en la rama `state` (un solo commit que se sobrescribe).

## Uso

- Ejecutar ahora: **Actions → Paper trading → Run workflow**.
- Empezar de cero: borra la rama `state`.
- En local: `pip install requests && python paper_trader.py` (bucle cada 20 s, dashboard en `dashboard.html`).

`strategy/gold_vantage.pine` es el script de TradingView con estas estrategias (elige la temporalidad del gráfico y aplica la suya; úsalo en `VANTAGE:XAUUSD`). `strategy/` también contiene el optimizador, los backtests y el script anterior (`trend_breakout.pine`).

> Simulación educativa. Resultados pasados no garantizan resultados futuros. No es asesoramiento financiero.
