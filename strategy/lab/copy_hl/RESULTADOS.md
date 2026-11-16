# Resultados: copiar carteras de Hyperliquid (examen del 2026-11-16)

Criterios en `PREREGISTRO.md`. Muestra de 1000 carteras al azar (955 con operaciones
copiables), 9 monedas (BTC, ETH, SOL, HYPE, XRP, ZEC, kPEPE, FARTCOIN, DOGE), retraso de
60 s, costes de 0,075 % por lado y funding real. Unas 50–160 carteras copiables cada mes.

## Diseño (2025-03 → 2025-12)
| | Rentab. | Caída | MAR |
|---|---|---|---|
| S1 Top rentabilidad 90 d | +8,9 % | 27,7 % | 0,39 |
| S2 Calidad (w = 0, rentab./caída) | −15,0 % | 34,9 % | −0,51 |
| S3 mejor combinación (w 0,5, X 15 %, f 1) | −39,2 % | 47,6 % | −0,94 |
| BTC comprar y mantener | +4,7 % | 34,8 % | 0,16 |
| Azar (mediana / p90) | −24,4 % / +11,3 % | | |

Las 27 combinaciones del detector pierden entre −39 y −48 %, frente al −15/−21 % de S2.

## Examen (2026-01-01 → 2026-11-15)
| | Rentab. | Caída | MAR | Supera al azar |
|---|---|---|---|---|
| S1 | −22,9 % | 48,2 % | −0,53 | |
| **S2** | **+23,3 %** | **18,0 %** | **1,51** | **98,6 %** |
| S3 | −0,5 % | 24,3 % | −0,02 | 94,4 % |
| BTC | −25,6 % | 40,3 % | −0,71 | |
| Azar (mediana / p90) | −40,4 % / −9,4 % | | | |

- **S3 (con detector) suspende** (A y C).
- **S2 (sin detector) aprueba** A, B y D.

## Comprobaciones (informativas, no cambian el veredicto)
- En el diseño S2 perdió y solo superó al 63 % de los sorteos al azar.
- Con 5 o 20 carteras en vez de 10, S2 en el examen da +0,1 % y +0,3 %.
- Por meses: −0,2, +5,8, +7,9, −4,3, **+27,8 (mayo)**, −6,6, −4,8, +9,1, +10,6, −10,1, −8,2 %.
  Sin mayo, cerca de 0.

## Conclusión
- Sustituir carteras por "cambio de estilo" **empeora** siempre: se sale tarde, después de
  la caída (como el selector "Meta" del oro).
- Copiar al azar pierde mucho (−24 a −40 %): la mayoría de las carteras pierde después de
  costes y retraso.
- Elegir por rentabilidad/caída (S2) bate claramente al azar en 2026, pero el aprobado es
  **frágil**: depende de k = 10 y de un mes. Es probable que sea en parte suerte.
- Siguiente paso coherente con el prerregistro: **paper trading de S2** (sin dinero) como
  examen limpio, con evaluación fijada de antemano.

## Aviso (descubierto el 2026-11-16, al montar el paper trading)
Muchas carteras guardan el saldo en la cuenta unificada (spot) y su valor de cuenta en
perpetuos aparece como 0 $. El simulador usaba entonces 1 $, así que el tamaño iba al tope de
3× la base: en el 6–59 % de las operaciones de las carteras elegidas se copió la dirección, no
el tamaño proporcional. El resultado vale para la regla tal como se programó. El paper trading
(`PREREGISTRO_VIVO.md`) la mantiene igual.
