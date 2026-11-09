# Paper trading · Trend Breakout (EMA + Donchian + ATR)

Simulación con **dinero ficticio** de una estrategia de seguimiento de tendencia en oro y BTC.
Se ejecuta sola en GitHub Actions cada ~10 minutos y publica el dashboard en GitHub Pages.

**Dashboard:** https://gxmezz.github.io/paper-trading-trend-breakout/

## Estrategia (3 indicadores)

| | Regla |
|---|---|
| EMA | Filtro de tendencia: solo largos si el cierre está por encima de la EMA |
| Donchian | Entrada: cierre por encima del máximo de las N velas anteriores |
| ATR(14) | Stop inicial a k×ATR y trailing stop (chandelier) que solo sube |

Riesgo del 1 % del capital por unidad, hasta 3 unidades (piramidación). Solo largos.

| Cuenta | Temporalidad | EMA | Donchian | Stop |
|---|---|---|---|---|
| Oro H1 | 1 hora | 200 | 40 | 3×ATR |
| Oro M30 | 30 min | 100 | 60 | 3×ATR |
| BTC Diario | 1 día | 200 | 20 | 3×ATR |

## Cómo funciona

- Precios de Binance (XAUUSDT perpetuo como oro, BTCUSDT spot), sin claves ni órdenes reales.
- Horario del oro: cerrado de viernes 21:00 a domingo 22:00 UTC y en la pausa diaria 21:00–22:00 UTC.
- Cada ejecución repite minuto a minuto lo ocurrido desde la anterior, así que el resultado no depende
  de que GitHub retrase las ejecuciones: las entradas se toman al open del minuto siguiente al cierre
  de la vela y los stops se comprueban con el mínimo de cada minuto.
- Costes: oro 0,25 $/oz por lado (estimación de Vantage Raw ECN), BTC 0,10 % por lado; swaps de
  6 %/año (oro, x3 el miércoles) y 20 %/año (BTC).
- El estado se guarda en la rama `state` (un solo commit que se sobrescribe).

## Uso

- Ejecutar ahora: pestaña **Actions → Paper trading → Run workflow**.
- Empezar de cero: borra la rama `state`.
- Cambiar la fuente del oro a PAXGUSDT: variable de repositorio `PT_GOLD_API = spot`.
- En local: `pip install requests && python paper_trader.py` (bucle cada 20 s, dashboard en `dashboard.html`).

`strategy/` contiene el script de TradingView (`trend_breakout.pine`) y los backtests.

> Simulación educativa. Resultados pasados no garantizan resultados futuros. No es asesoramiento financiero.
