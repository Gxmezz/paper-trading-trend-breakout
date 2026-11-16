# Evaluación en vivo (paper trading) de la copia S2

Fijada el 2026-11-16, antes de arrancar. No se cambia después.

## Qué se prueba
La regla S2 que aprobó el examen (`RESULTADOS.md`), sin tocar nada:
- Día 1 de cada mes: muestra al azar de 1000 carteras del ranking (volumen de 10 M a 5 000 M $),
  filtro de copiables y las 10 con mejor rentabilidad/caída de su copia simulada en 180 días.
- Copia en 9 monedas (BTC, ETH, SOL, HYPE, XRP, ZEC, kPEPE, FARTCOIN, DOGE), 100 € por
  cartera, tope de 3× la base, comisión 0,045 % + deslizamiento 0,03 % por lado y funding.
- Solo se opera cuando la cartera cambia una posición. El valor de su cuenta se mide una vez
  por semana; si es ≤ 1 $ se usa 1 $, igual que en el examen, y el tamaño va al tope de 3×.

Control: 10 carteras copiables al azar con las mismas reglas.

## Aviso sobre el examen
Muchas carteras tienen el saldo en la cuenta unificada (spot) y su valor en perpetuos aparece
como 0 $. En el examen, entre el 6 y el 59 % de las operaciones de las carteras elegidas se
copiaron así, con el tamaño en el tope de 3×: en esos casos se copió la dirección, no el tamaño.
Se mantiene igual en vivo para examinar exactamente la regla que aprobó.

## Periodo y criterios (todos)
- Del **2026-11-17 al 2027-02-16** (3 meses).
- **A**: la cuenta S2 gana dinero.
- **B**: la cuenta S2 acaba por encima del control al azar.
- **C**: caída máxima de S2 ≤ 30 %.
- Informativo: comparación con BTC, órdenes por día y % de órdenes por debajo del mínimo real
  de Hyperliquid (10 $), que decide si se puede operar de verdad con ~1000 €.

Si suspende, se descarta y no se pasa a dinero real.
