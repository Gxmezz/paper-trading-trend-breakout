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
# Fase 3: el gestor (MODEL_MGMT, Sonnet 5.5) revisa la operación abierta en cada cierre de vela M15.
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

def _call(model, system, schema, context, base, spent_today, effort="medium"):
    """Llamada común a la API: formato JSON fijo, respaldo ante rechazos, coste y errores sin excepciones."""
    if (os.environ.get("PT_AGENTS") or "on").lower() == "off":
        return {**base, "error": "agentes desactivados (PT_AGENTS=off)"}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {**base, "error": "falta ANTHROPIC_API_KEY"}
    if spent_today >= DAILY_CAP_USD:
        return {**base, "error": f"tope de gasto diario alcanzado ({spent_today:.2f} $ de {DAILY_CAP_USD:.2f} $)"}
    import anthropic
    t0 = time.time(); ms = lambda: int((time.time() - t0) * 1000)
    try:
        client = anthropic.Anthropic(max_retries=2, timeout=120)
        resp = client.beta.messages.create(
            model=model, max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",   # si el modelo rechaza, otro modelo responde
            system=system,
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": json.dumps(context, ensure_ascii=False, sort_keys=True)}],
        )
        c = cost_usd(model, resp.usage)
        if resp.stop_reason == "refusal": return {**base, "cost": c, "error": "el modelo rechazó la petición", "ms": ms()}
        if resp.stop_reason == "max_tokens": return {**base, "cost": c, "error": "respuesta cortada (max_tokens)", "ms": ms()}
        out = json.loads(next(b.text for b in resp.content if b.type == "text"))
        return {**base, **out, "cost": c, "model": getattr(resp, "model", model), "ms": ms()}
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

def _mocking():
    return bool(os.environ.get("PT_AGENT_MOCK")) and (os.environ.get("PT_AGENTS") or "on").lower() != "off"

def decide_entry(context, spent_today):
    """Devuelve dict(decision, stop, targets, break_even, confidence, reason, cost, model, error)."""
    base = dict(decision="no_entrar", stop=None, targets=[], break_even=True, confidence=0.0, reason="", cost=0.0,
                model=MODEL_ENTRY, error=None, ms=0)
    if _mocking(): return {**base, **_mock(context), "model": "simulado"}
    return _call(MODEL_ENTRY, SYSTEM_ENTRY, SCHEMA_ENTRY, context, base, spent_today)

# ---------- fase 3: AGENTE GESTOR de la operación abierta ----------
MODEL_MGMT = "claude-sonnet-5-5"

SYSTEM_MGMT = """Eres el agente GESTOR de operaciones de una cuenta de paper trading (dinero ficticio) de oro (XAUUSD, Vantage) que opera la estrategia SMC (Smart Money Concepts). Otro agente decidió la entrada; tú revisas la operación abierta al cierre de cada vela M15 y decides qué hacer. Hay una cuenta gemela mecánica que gestiona con reglas fijas (objetivos fijos y break even tras el primero, sin intervenir nunca); el experimento mide si la gestión con criterio mejora a esas reglas fijas, así que intervenir sin un motivo claro también se paga (costes y operaciones cortadas antes de tiempo).

Acciones posibles (se aplican a partir de la siguiente vela M15):
- "mantener": no cambiar nada. Es lo normal cuando no hay un motivo concreto.
- "mover_stop": nuevo stop en nuevo_stop. Solo puede ACERCARSE al precio (reducir riesgo) y debe quedar al lado correcto del precio actual: un largo no puede bajar el stop y un corto no puede subirlo. Si lo incumples, el código lo rechaza.
- "break_even": stop al precio de entrada (solo si el precio actual lo permite).
- "cambiar_objetivos": nuevos precios objetivo en nuevos_objetivos, uno por cada parte abierta (mismo número), al lado correcto del precio.
- "cerrar_parcial": cierra la parte más cercana al precio a la apertura de la siguiente vela.
- "cerrar": cierra toda la posición a la apertura de la siguiente vela.

Las reglas de riesgo (2 % por operación, -5 % diario, 4 pérdidas paran el día) las aplica el código y no se pueden ampliar.

Responde con: accion, nuevo_stop (repite el stop actual si no lo cambias), nuevos_objetivos (repite los actuales si no los cambias) y reason (español, máximo 300 caracteres, los factores decisivos). No inventes datos que no estén en el contexto."""

SCHEMA_MGMT = {
    "type": "object",
    "properties": {
        "accion": {"type": "string", "enum": ["mantener", "mover_stop", "break_even", "cambiar_objetivos", "cerrar_parcial", "cerrar"]},
        "nuevo_stop": {"type": "number"},
        "nuevos_objetivos": {"type": "array", "items": {"type": "number"}},
        "reason": {"type": "string"},
    },
    "required": ["accion", "nuevo_stop", "nuevos_objetivos", "reason"],
    "additionalProperties": False,
}

def _mock_mgmt(context):
    p = context["posicion"]
    if p["R_ahora"] >= 1.0 and not p["break_even_hecho"]:
        return dict(accion="break_even", nuevo_stop=p["entrada"], nuevos_objetivos=p["objetivos"], reason="Simulación: BE a +1R.")
    if p["velas_en_operacion"] >= 12:
        return dict(accion="cerrar", nuevo_stop=p["stop"], nuevos_objetivos=p["objetivos"], reason="Simulación: 3 h sin llegar.")
    return dict(accion="mantener", nuevo_stop=p["stop"], nuevos_objetivos=p["objetivos"], reason="Simulación: mantener.")

def decide_manage(context, spent_today):
    """Devuelve dict(accion, nuevo_stop, nuevos_objetivos, reason, cost, model, error)."""
    p = context["posicion"]
    base = dict(accion="mantener", nuevo_stop=p["stop"], nuevos_objetivos=p["objetivos"], reason="", cost=0.0,
                model=MODEL_MGMT, error=None, ms=0)
    if _mocking(): return {**base, **_mock_mgmt(context), "model": "simulado"}
    return _call(MODEL_MGMT, SYSTEM_MGMT, SCHEMA_MGMT, context, base, spent_today)

def selftest():
    import anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"): sys.exit("Falta ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(max_retries=2, timeout=60)
    r = client.messages.create(model=MODEL_SELFTEST, max_tokens=2000, output_config={"effort": "low"},
                               messages=[{"role": "user", "content": "Responde solo: OK"}])
    text = next((b.text for b in r.content if b.type == "text"), "")
    print(f"Clave válida. Modelo: {r.model} · respuesta: {text.strip()[:40]!r} · coste: {cost_usd(MODEL_SELFTEST, r.usage):.5f} $")

def selftest_entry():
    """Prueba la llamada real del agente de entrada con un setup de ejemplo (coste ~0,05-0,10 $)."""
    ctx = dict(setup=dict(lado="LARGO", zona=[4040.9, 4045.1], stop_mecanico=4039.7, cierre=4046.7, atr_m15=5.6,
                          filtros_que_fallan=[], la_gemela_lo_opera=True, objetivos_gemela_R=[2, 3, 4]),
               contexto=dict(hora_utc="2026-11-11 12:15", sesgo_estructura_H4="alcista", ema200_H1=4010.0,
                             sma9_H1=4038.0, sma21_H1=4030.0, precio_ahora=4046.7, nota="CONTEXTO DE PRUEBA, sin velas"),
               cuenta=dict(capital_eur=1000.0, resultado_hoy_pct=0.0, perdidas_hoy=0, ultimas_operaciones=[]))
    r = decide_entry(ctx, 0.0)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if r["error"]: sys.exit(f"ERROR: {r['error']}")

def selftest_manage():
    """Prueba la llamada real del agente gestor con una posición de ejemplo (coste ~0,01-0,03 $)."""
    ctx = dict(posicion=dict(lado="LARGO", entrada=4046.7, stop=4039.7, break_even_hecho=False, objetivos=[4060.7, 4067.7, 4074.7],
                             oz_por_objetivo=[1, 1, 1], R_ahora=1.1, R_max=1.4, R_min=-0.3, velas_en_operacion=5,
                             motivo_de_entrada="Largo a favor del sesgo H4 en killzone de NY."),
               contexto=dict(hora_utc="2026-11-11 13:30", precio_ahora=4054.4, nota="CONTEXTO DE PRUEBA, sin velas"))
    r = decide_manage(ctx, 0.0)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if r["error"]: sys.exit(f"ERROR: {r['error']}")

if __name__ == "__main__":
    if "--selftest-manage" in sys.argv: selftest_manage()
    if "--selftest" in sys.argv: selftest()
    if "--selftest-entry" in sys.argv: selftest_entry()
