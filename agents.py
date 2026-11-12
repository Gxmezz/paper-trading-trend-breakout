"""
Agentes del experimento SMC (paper trading). Fase 2: AGENTE DE ENTRADA.

Cuando el detector SMC confirma un setup, el agente recibe el contexto (velas M15/H1/H4, zona, stop mecánico, filtros,
indicadores, estado de la cuenta) y decide si entrar, con qué stop y con qué objetivos. Su respuesta tiene un formato
fijo (JSON validado por la API) y después la valida el guardián de riesgo (strategy/risk_guard.py): el agente NO puede
saltarse las reglas de riesgo.

Coste: cada llamada se registra con su coste en $ (precios por millón de tokens en PRICES). Si el gasto del día supera
PT_AGENT_DAILY_USD (2 $ por defecto), no se llama al agente hasta el día siguiente (el setup se registra sin operar).
Interruptor: PT_AGENTS=off desactiva todas las llamadas. PT_AGENT_MOCK=1 simula respuestas (pruebas locales sin coste).

La clave se lee de la variable de entorno ANTHROPIC_API_KEY (secreto del repositorio en GitHub Actions).
Prueba de la clave: python agents.py --selftest  (una llamada mínima con Haiku 5.5, coste < 0,01 $)
"""
import json, os, sys, time

MODEL_ENTRY = "claude-opus-5-5"
MODEL_SELFTEST = "claude-haiku-5-5"
# $ por millón de tokens: (entrada, salida, lectura de caché, escritura de caché 5 min)
PRICES = {"claude-opus-5-5": (4.0, 20.0, 0.20, 5.0), "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5),
          "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125)}
DAILY_CAP_USD = float(os.environ.get("PT_AGENT_DAILY_USD") or 2.0)

SYSTEM_ENTRY = """Eres el agente de ENTRADAS de una cuenta de paper trading (dinero ficticio) de oro (XAUUSD, Vantage) que opera la estrategia SMC (Smart Money Concepts) de un curso: sesgo por estructura de mercado en H4, Order Blocks con FVG creados tras una ruptura de estructura en M15, entrada tras confirmar la reacción en la zona.

Un detector mecánico acaba de confirmar un setup. Tu trabajo: decidir si se entra y, si se entra, dónde poner el stop y los objetivos. Hay una cuenta gemela mecánica que opera los mismos setups con reglas fijas (solo a favor del sesgo H4 y de la EMA 200 de H1, solo en killzones de Londres 07-10 y Nueva York 12-16 UTC, stop tras la zona + 0,2 ATR, objetivos 2R/3R/4R con break even tras el primero). El experimento mide si tu criterio mejora a esas reglas fijas, así que puedes discrepar de ellas en cualquier sentido cuando tengas motivos: entrar en un setup que la gemela descarta o no entrar en uno que la gemela opera.

Reglas que NO dependen de ti (las aplica el código después de tu respuesta y no se pueden saltar):
- Riesgo 2 % del capital por operación, tamaño en onzas enteras; si 1 oz arriesga más del 2 %, no se opera. Un stop más lejano no aumenta el riesgo en euros, reduce el tamaño.
- Pérdida diaria máxima 5 %; 4 pérdidas en el día paran el día.
- La media de los objetivos tiene que estar al menos a 2R (2 veces la distancia al stop). Si no, la entrada se rechaza.
- La entrada se ejecuta al precio de apertura de la siguiente vela M15. Costes 0,20 $/oz por lado; swap de largos 6 %/año.
- Solo puedes operar en la dirección del setup.

Cómo responder:
- decision: "entrar" o "no_entrar".
- stop: precio del stop (por debajo de la entrada en largos, por encima en cortos). Si no entras, repite el stop mecánico.
- targets: de 1 a 3 precios objetivo, del más cercano al más lejano, al lado correcto. La posición se reparte entre ellos. Si no entras, lista vacía.
- break_even: true para mover el stop a la entrada al tocar el primer objetivo.
- confidence: de 0 a 1.
- reason: explicación breve en español (máximo 400 caracteres) con los factores decisivos.

Sé disciplinado: la mayoría de setups SMC no tienen ventaja; entrar solo cuando el contexto lo justifique es tan importante como acertar la dirección. No inventes datos que no estén en el contexto."""

SCHEMA_ENTRY = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["entrar", "no_entrar"]},
        "stop": {"type": "number"},
        "targets": {"type": "array", "items": {"type": "number"}},
        "break_even": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["decision", "stop", "targets", "break_even", "confidence", "reason"],
    "additionalProperties": False,
}

def cost_usd(model, usage):
    pin, pout, pcr, pcw = PRICES.get(model, PRICES[MODEL_ENTRY])
    g = lambda k: getattr(usage, k, 0) or 0
    return (g("input_tokens") * pin + g("output_tokens") * pout + g("cache_read_input_tokens") * pcr
            + g("cache_creation_input_tokens") * pcw) / 1e6

def _mock(context):
    s = context["setup"]; d = 1 if s["lado"] == "LARGO" else -1
    entry = s["cierre"]; stop = s["stop_mecanico"]; dist = abs(entry - stop)
    ok = not s["filtros_que_fallan"]
    return dict(decision="entrar" if ok else "no_entrar", stop=stop,
                targets=[round(entry + d * k * dist, 2) for k in (2, 3, 4)] if ok else [], break_even=True,
                confidence=0.5, reason="Simulación local (PT_AGENT_MOCK): entra solo si no falla ningún filtro.")

def decide_entry(context, spent_today):
    """Devuelve dict(decision, stop, targets, break_even, confidence, reason, cost, model, error)."""
    base = dict(decision="no_entrar", stop=None, targets=[], break_even=True, confidence=0.0, reason="", cost=0.0,
                model=MODEL_ENTRY, error=None, ms=0)
    if (os.environ.get("PT_AGENTS") or "on").lower() == "off":
        return {**base, "error": "agentes desactivados (PT_AGENTS=off)"}
    if os.environ.get("PT_AGENT_MOCK"):
        return {**base, **_mock(context), "model": "simulado"}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {**base, "error": "falta ANTHROPIC_API_KEY"}
    if spent_today >= DAILY_CAP_USD:
        return {**base, "error": f"tope de gasto diario alcanzado ({spent_today:.2f} $ de {DAILY_CAP_USD:.2f} $)"}
    import anthropic
    t0 = time.time()
    try:
        client = anthropic.Anthropic(max_retries=2, timeout=120)
        resp = client.beta.messages.create(
            model=MODEL_ENTRY, max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",   # si el modelo rechaza, otro modelo responde
            system=SYSTEM_ENTRY,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA_ENTRY}},
            messages=[{"role": "user", "content": json.dumps(context, ensure_ascii=False, sort_keys=True)}],
        )
        c = cost_usd(MODEL_ENTRY, resp.usage)
        if resp.stop_reason == "refusal":
            return {**base, "cost": c, "error": "el modelo rechazó la petición", "ms": int((time.time() - t0) * 1000)}
        if resp.stop_reason == "max_tokens":
            return {**base, "cost": c, "error": "respuesta cortada (max_tokens)", "ms": int((time.time() - t0) * 1000)}
        text = next(b.text for b in resp.content if b.type == "text")
        out = json.loads(text)
        return {**base, **out, "cost": c, "model": getattr(resp, "model", MODEL_ENTRY), "ms": int((time.time() - t0) * 1000)}
    except anthropic.AuthenticationError:
        return {**base, "error": "clave de API no válida"}
    except anthropic.RateLimitError:
        return {**base, "error": "límite de peticiones de la API"}
    except anthropic.APIStatusError as e:
        return {**base, "error": f"error de la API {e.status_code}: {str(e.message)[:200]}"}
    except anthropic.APIConnectionError:
        return {**base, "error": "sin conexión con la API"}
    except (json.JSONDecodeError, StopIteration) as e:
        return {**base, "error": f"respuesta no válida: {e!r}"[:200]}

def selftest():
    import anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"): sys.exit("Falta ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(max_retries=2, timeout=60)
    r = client.messages.create(model=MODEL_SELFTEST, max_tokens=2000, output_config={"effort": "low"},
                               messages=[{"role": "user", "content": "Responde solo: OK"}])
    text = next((b.text for b in r.content if b.type == "text"), "")
    print(f"Clave válida. Modelo: {r.model} · respuesta: {text.strip()[:40]!r} · coste: {cost_usd(MODEL_SELFTEST, r.usage):.5f} $")

if __name__ == "__main__":
    if "--selftest" in sys.argv: selftest()
