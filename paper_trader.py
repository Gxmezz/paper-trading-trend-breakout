"""
Paper trading (dinero ficticio) de estrategias para ORO en Vantage, cuenta de 1000 € por estrategia.

Estrategias (máximo 3 indicadores cada una), optimizadas por temporalidad (ver strategy/vantage_opt.py):
  BO  : EMA + ruptura Donchian + stop ATR con trailing           (H1)
  BOH : EMA de H1 (tendencia superior) + Donchian + ATR trailing   (M30, con piramidación)
  PB  : EMA de H1 + RSI en sobreventa (pullback) + stop ATR fijo; salida cuando el RSI se recupera (M15, M5)

Realismo:
  - Cuenta en EUR (1000 €). Los resultados en USD se convierten al EURUSD del momento (Binance EURUSDT).
  - XAUUSD estándar de Vantage: 0.01 lote = 1 oz -> posiciones en onzas enteras. Si 1 oz arriesga más
    del 4 % de la cuenta, la señal se descarta.
  - Costes 0.20 $/oz por lado (Raw ECN: medio spread + comisión 3 $/lote/lado + slippage).
    Swap de largos 6 %/año (miércoles x3), cortos 0. Apalancamiento máximo 1:50.
  - Horario del oro (hora de Nueva York, se ajusta solo al horario de verano/invierno): cerrado de vie 17:00
    a dom 18:00 y pausa diaria 17:00-18:00 (en Madrid, normalmente 23:00-00:00).
  - Precio del oro: perpetuo XAU_USDT de Gate.io (cotiza como el oro spot; accesible desde cualquier país).
  - Cada ejecución repite minuto a minuto lo ocurrido desde la anterior: entradas y salidas al open del minuto
    siguiente al cierre de la vela, stops con el mínimo/máximo de cada minuto. Resultado independiente de la
    frecuencia con la que se ejecute.

Uso:  python paper_trader.py          (bucle continuo, cada 20 s)
      python paper_trader.py --once   (una pasada; así lo ejecuta GitHub Actions)
      python paper_trader.py --reset  (empieza de cero)
"""
import json, math, os, sys, time, traceback, html, bisect
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import requests

HERE = Path(__file__).parent
STATE = Path(os.environ.get("PT_STATE", HERE / "state.json"))
DASH = Path(os.environ.get("PT_DASH", HERE / "dashboard.html"))
LOG = Path(os.environ.get("PT_LOG", HERE / "paper_trader.log"))
GOLD_API = os.environ.get("PT_GOLD_API", "gate")
GOLD_SYMBOL = {"gate": "XAU_USDT", "fapi": "XAUUSDT", "spot": "PAXGUSDT"}[GOLD_API]
POLL_SECONDS = 20
VERSION = 2

CAPITAL_EUR = 1000.0
COST_SIDE = 0.20     # $/oz por lado
SWAP_LONG = 0.06     # anual
STEP = 1.0           # oz (0.01 lote)
MAX_RISK = 0.04      # riesgo máximo aceptado con el lote mínimo
MAX_LEV = 50.0

ACCOUNTS = [
    dict(id="gold_h1", name="Oro H1 · Ruptura", tf="1h", fam="BO", ema=200, don=10, mult=2.0, units=1,
         shorts=True, sess=(0, 24), risk=0.01,
         desc="EMA 200 + ruptura Donchian 10 + stop ATR×2 con trailing · largos y cortos"),
    dict(id="gold_m30", name="Oro M30 · Ruptura con filtro H1", tf="30m", fam="BOH", htf="1h", ema=200, don=60,
         mult=3.0, units=3, shorts=True, sess=(0, 24), risk=0.01, news=True,
         desc="EMA 200 de H1 + ruptura Donchian 60 + stop ATR×3 con trailing · hasta 3 unidades · largos y cortos · sin entradas en noticias de EE. UU."),
    dict(id="gold_m30_sniper", name="Oro M30 · Ruptura + filtro Sniper (prueba)", tf="30m", fam="BOH", htf="1h", ema=200,
         don=60, mult=3.0, units=3, shorts=True, sess=(0, 24), risk=0.01, news=True, sniper=True,
         desc="Como Oro M30 + solo a favor de SMA 9 > SMA 21 en H1 (indicador QNA Sniper) · cuenta experimental"),
    dict(id="gold_m15", name="Oro M15 · Pullback", tf="15m", fam="PB", htf="1h", ema=200, rsi=2, lo=20, hi=80,
         mult=2.0, shorts=False, sess=(7, 20), risk=0.01,
         desc="Tendencia EMA 200 de H1 + compra con RSI(2) < 20, venta con RSI(2) > 80 · stop ATR×2 · entradas 07-20 UTC ({SESS_MAD})"),
    dict(id="gold_smc_mec", name="Oro M15 · SMC mecánica (control)", tf="15m", fam="SMC", risk=0.02,
         smc=dict(n=3, nh=3, imp=1.5, zone="ob", sweep=0, bias="4h", ltf_align=False, filt="ema", conf="close",
                  conf_win=4, age=96, sl=0.2, pd=False, sess="kz", exit="R234", news="block", tp=(2, 3, 4)),
         desc="SMC del curso, mecánica (strategy/smc.py v2, la mejor de entrenamiento): sesgo de estructura H4 + EMA 200 H1 · "
              "Order Block con FVG tras ruptura de estructura · entrada al confirmar con cierre · stop tras el OB + 0,2 ATR · "
              "3 parciales a 2R/3R/4R con break even · killzones Londres y NY · sin entradas en noticias · riesgo 2 %, "
              "−5 % diario, 4 pérdidas paran el día. Cuenta de control para el futuro equipo de agentes."),
    dict(id="gold_smc_agents", name="Oro M15 · SMC agentes (experimento)", tf="15m", fam="SMC_AGENT", risk=0.02,
         smc=dict(n=3, nh=3, imp=1.5, zone="ob", sweep=0, bias="4h", ltf_align=False, filt="ema", conf="close",
                  conf_win=4, age=96, sl=0.2, pd=False, sess="kz", exit="R234", news="block", tp=(2, 3, 4)),
         desc="Los mismos setups SMC que la cuenta mecánica, pero un agente (Claude Opus 5.5) decide si entrar, el stop y los "
              "objetivos, viendo también los setups que la mecánica descarta. Mismo guardián de riesgo: 2 % por operación, "
              "−5 % diario, 4 pérdidas paran el día, ratio ≥ 2:1. Sin entradas en ventanas de noticias. Fase 3: un agente gestor "
              "(Claude Sonnet 5.5) revisa la operación abierta al cierre de cada vela M15 y puede acercar el stop, poner break even, "
              "mover objetivos o cerrar (todo o una parte)."),
]
TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}
GATE = "https://api.gateio.ws/api/v4/futures/usdt"
BINANCE = {"fapi": "https://fapi.binance.com/fapi/v1", "spot": "https://data-api.binance.vision/api/v3"}

def now_utc(): return datetime.now(timezone.utc)
def ms_to_dt(ms): return datetime.fromtimestamp(ms / 1000, timezone.utc)
def log(msg):
    line = f"{now_utc():%Y-%m-%d %H:%M:%S} UTC  {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f: f.write(line + "\n")

# ---------- horario ----------
NY = ZoneInfo("America/New_York")
MAD = ZoneInfo("Europe/Madrid")
def madrid(t: datetime) -> datetime: return t.astimezone(MAD)
def iso_madrid(s: str) -> str:
    """'2026-11-09T20:02:00+00:00' -> '09/11 21:02' (hora de Madrid)"""
    try: return madrid(datetime.fromisoformat(s)).strftime("%d/%m %H:%M")
    except Exception: return s or ""

# Filtro de noticias (solo cuentas con news=True): sin entradas en velas que empiezan de 08:00 a 08:45 NY
# (datos de EE. UU. a las 08:30) ni, en días de FOMC, de 13:30 a 15:15 NY. Igual que strategy/smc.news_masks.
FOMC = {"2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28",
        "2026-12-09"}   # añadir las fechas de 2027 cuando la Fed publique el calendario
def news_block(bar_ms: int) -> bool:
    ny = ms_to_dt(bar_ms).astimezone(NY); m = ny.hour * 60 + ny.minute
    if ny.weekday() < 5 and 8 * 60 <= m < 8 * 60 + 45: return True
    return ny.strftime("%Y-%m-%d") in FOMC and 13 * 60 + 30 <= m < 15 * 60 + 15

def gold_open(t: datetime) -> bool:
    n = t.astimezone(NY); wd, h = n.weekday(), n.hour
    if wd == 5: return False                    # sábado
    if wd == 4 and h >= 17: return False        # viernes desde las 17:00 NY
    if wd == 6 and h < 18: return False         # domingo hasta las 18:00 NY
    if h == 17: return False                    # pausa diaria 17:00-18:00 NY
    return True

def trading_day(t: datetime) -> str:
    """Día de negociación: cambia en el rollover de las 17:00 NY (cuando se cobra el swap)."""
    return (t.astimezone(NY) + timedelta(hours=7)).date().isoformat()

# ---------- datos ----------
def klines(interval, limit=1000, start=None):
    if GOLD_API == "gate":
        sec = TF_MS[interval] // 1000
        p = {"contract": GOLD_SYMBOL, "interval": interval}
        if start is None: p["limit"] = min(limit, 1999)
        else:
            p["from"] = int(start // 1000)
            p["to"] = int(min(start // 1000 + (min(limit, 1999) - 1) * sec, time.time()))
        r = requests.get(f"{GATE}/candlesticks", params=p, timeout=20); r.raise_for_status()
        d = OFFSET["value"] or 0.0      # corrección Gate -> spot (Vantage)
        return [dict(t=int(k["t"]) * 1000, o=float(k["o"]) - d, h=float(k["h"]) - d, l=float(k["l"]) - d, c=float(k["c"]) - d,
                     ct=int(k["t"]) * 1000 + sec * 1000 - 1) for k in r.json()]
    p = {"symbol": GOLD_SYMBOL, "interval": interval, "limit": limit}
    if start is not None: p["startTime"] = int(start)
    r = requests.get(f"{BINANCE[GOLD_API]}/klines", params=p, timeout=20); r.raise_for_status()
    return [dict(t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), ct=int(k[6])) for k in r.json()]

def closed_bars(interval, limit=1000, start=None):
    nowms = time.time() * 1000
    return [b for b in klines(interval, limit, start) if b["ct"] < nowms and gold_open(ms_to_dt(b["t"]))]

# ---------- corrección al precio spot (≈ Vantage) ----------
# El perpetuo de Gate se separa a veces del oro spot (p. ej. ~7 $ cerca del cierre diario). Cada ejecución mide
# la diferencia Gate - spot con las cotizaciones públicas de Swissquote (bróker; coincide con VANTAGE:XAUUSD al
# céntimo) y la resta a todas las velas y precios de Gate.
OFFSET = {"value": 0.0}

def spot_ref():
    """Precio bid del oro spot (como los gráficos de MT5/Vantage). Swissquote; respaldo gold-api.com."""
    try:
        r = requests.get("https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD", timeout=10)
        r.raise_for_status()
        profs = [p for q in r.json() for p in q.get("spreadProfilePrices", [])]
        best = min(profs, key=lambda p: p["ask"] - p["bid"])
        return float(best["bid"]), "Swissquote"
    except Exception:
        pass
    try:
        r = requests.get("https://api.gold-api.com/price/XAU", timeout=10); r.raise_for_status()
        return float(r.json()["price"]), "gold-api.com"
    except Exception:
        return None, None

def gate_raw_last():
    r = requests.get(f"{GATE}/tickers", params={"contract": GOLD_SYMBOL}, timeout=15); r.raise_for_status()
    return float(r.json()[0]["last"])

def update_offset(s):
    """Actualiza s['offset'] (media móvil de la diferencia Gate - spot) solo con el mercado abierto."""
    info = s.setdefault("offset_info", {})
    if GOLD_API == "gate" and gold_open(now_utc()):
        try:
            ref, src = spot_ref(); g = gate_raw_last()
            if ref and abs(g - ref) < 60:
                sample = g - ref
                prev = s.get("offset")
                s["offset"] = sample if prev is None else 0.5 * prev + 0.5 * sample
                info.update(ref=ref, src=src, gate=g, sample=round(sample, 2), at=now_utc().isoformat())
        except Exception as ex:
            log(f"offset: sin referencia ({ex!r})")
    OFFSET["value"] = s.get("offset") or 0.0
    # migración única: posiciones abiertas con precios de Gate sin corregir -> misma escala
    if not s.get("offset_migrated") and OFFSET["value"]:
        d = OFFSET["value"]
        for st in s["accounts"].values():
            st["units"] = [[q, e - d, w] for q, e, w in st["units"]]
            if st.get("stop") is not None: st["stop"] -= d
        s["offset_migrated"] = True
        log(f"Posiciones abiertas pasadas a precio spot (corrección {d:+.2f} $)")

def last_price():
    if GOLD_API == "gate":
        r = requests.get(f"{GATE}/tickers", params={"contract": GOLD_SYMBOL}, timeout=15); r.raise_for_status()
        return float(r.json()[0]["last"]) - (OFFSET["value"] or 0.0)
    r = requests.get(f"{BINANCE[GOLD_API]}/ticker/price", params={"symbol": GOLD_SYMBOL}, timeout=15); r.raise_for_status()
    return float(r.json()["price"])

def eurusd(prev):
    try:
        r = requests.get(f"{BINANCE['spot']}/ticker/price", params={"symbol": "EURUSDT"}, timeout=15); r.raise_for_status()
        return float(r.json()["price"])
    except Exception:
        return prev or 1.12

# ---------- indicadores (equivalentes a TradingView / pandas) ----------
def ema(vals, n):
    a = 2 / (n + 1); out = []; e = vals[0]
    for v in vals: e = a * v + (1 - a) * e; out.append(e)
    return out

def atr(bars, n=14):
    out = []; a = None; pc = None
    for b in bars:
        tr = b["h"] - b["l"] if pc is None else max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        a = tr if a is None else a + (tr - a) / n
        out.append(a); pc = b["c"]
    return out

def rsi(vals, n):
    out = [50.0]; au = ad = None
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]; u, dn = max(d, 0.0), max(-d, 0.0)
        au = u if au is None else au + (u - au) / n
        ad = dn if ad is None else ad + (dn - ad) / n
        out.append(100.0 if ad == 0 else 100 - 100 / (1 + au / ad))
    return out

# ---------- estado ----------
def new_account_state():
    return dict(cash=CAPITAL_EUR, pos=0, units=[], stop=None, last_1m=None, last_sig_bar=None, last_swap_day=None,
                price=None, equity=CAPITAL_EUR, peak=CAPITAL_EUR, max_dd=0.0, trades=[], curve=[], events=[],
                ind={}, status="iniciando", swaps_paid=0.0, costs_paid=0.0, trade_open=None, pend_exit=False,
                skipped=0)

def load_state():
    s = None
    raw = STATE.read_text(encoding="utf-8").strip() if STATE.exists() else ""
    if raw and "--reset" not in sys.argv:
        s = json.loads(raw)
        if s.get("version") != VERSION:
            log("Estado de una versión anterior: se empieza de cero"); s = None
    if s is None:
        s = dict(version=VERSION, started=now_utc().isoformat(), accounts={}, fx=None)
    for k in [k for k in s["accounts"] if k not in {a["id"] for a in ACCOUNTS}]:   # cuentas retiradas: se archivan
        s.setdefault("archived", {})[k] = dict(s["accounts"].pop(k), archived_at=now_utc().isoformat())
        log(f"Cuenta {k} retirada y archivada en el estado")
    s["accounts"] = {a["id"]: s["accounts"].get(a["id"]) or new_account_state() for a in ACCOUNTS}
    return s

def save_state(s):
    tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(s, indent=1), encoding="utf-8"); tmp.replace(STATE)

def event(st, msg):
    st["events"].insert(0, f"{madrid(now_utc()):%d/%m %H:%M} · {msg}"); del st["events"][60:]

def unreal_usd(st, px): return sum(st["pos"] * q * (px - e) for q, e, _ in st["units"])
def equity_eur(st, px, fx): return st["cash"] + (unreal_usd(st, px) / fx if px else 0.0)

# ---------- operativa ----------
def close_position(acc, st, px_raw, when, reason, fx):
    pos = st["pos"]; px = px_raw - pos * COST_SIDE
    qty = sum(q for q, _, _ in st["units"])
    pnl_usd = sum(pos * q * (px - e) for q, e, _ in st["units"])
    st["cash"] += pnl_usd / fx; st["costs_paid"] += qty * COST_SIDE / fx
    t = st["trade_open"] or {}
    total_eur = pnl_usd / fx - t.get("swaps", 0)
    st["trades"].insert(0, dict(side="LARGO" if pos == 1 else "CORTO", entry_time=t.get("time"), exit_time=when,
                                units=len(st["units"]), qty=qty,
                                avg_entry=round(sum(q * e for q, e, _ in st["units"]) / qty, 2), exit=round(px_raw, 2),
                                pnl=round(total_eur, 2), R=round(total_eur / (t.get("risk") or 1), 2), reason=reason))
    event(st, f"CIERRE {'largo' if pos == 1 else 'corto'} {qty:g} oz a {px_raw:,.2f} ({reason}) · {total_eur:+,.2f} €")
    log(f"[{acc['id']}] cierre {pos} {qty} @ {px_raw:.2f} {reason} {total_eur:+.2f} EUR")
    st["units"] = []; st["pos"] = 0; st["stop"] = None; st["trade_open"] = None; st["pend_exit"] = False

def open_unit(acc, st, side, px_raw, dist, when, fx):
    eq_usd = equity_eur(st, px_raw, fx) * fx
    q = math.floor(eq_usd * acc["risk"] / dist / STEP + 1e-9) * STEP
    if q < STEP:
        if STEP * dist <= eq_usd * MAX_RISK: q = STEP
        else:
            st["skipped"] += 1
            event(st, f"Señal descartada: 1 oz arriesgaría {STEP * dist / fx:,.0f} € (> {MAX_RISK:.0%} de la cuenta)"); return
    expo = sum(qq for qq, _, _ in st["units"]) * px_raw
    if expo + q * px_raw > eq_usd * MAX_LEV:
        event(st, "Señal descartada: superaría el apalancamiento máximo"); return
    fill = px_raw + side * COST_SIDE
    st["units"].append([q, fill, when]); st["costs_paid"] += q * COST_SIDE / fx
    new_stop = px_raw - side * dist
    if st["pos"] == 0: st["pos"] = side; st["stop"] = new_stop
    else: st["stop"] = max(st["stop"], new_stop) if side == 1 else min(st["stop"], new_stop)
    if st["trade_open"] is None: st["trade_open"] = dict(time=when, risk=0.0, swaps=0.0)
    st["trade_open"]["risk"] += q * dist / fx
    lado = "COMPRA" if side == 1 else "VENTA"
    extra = f" unidad {len(st['units'])}/{acc.get('units', 1)}" if acc.get("units", 1) > 1 else ""
    event(st, f"{lado}{extra}: {q:g} oz a {fill:,.2f} · stop {st['stop']:,.2f} · riesgo {q * dist / fx:,.0f} €")
    log(f"[{acc['id']}] {lado} {q} @ {fill:.2f} stop={st['stop']:.2f}")

def build_indicators(acc, bars, htf_bars):
    closes = [b["c"] for b in bars]
    A = atr(bars)
    if acc["fam"] == "BO" or acc.get("htf") == "same":
        T = ema(closes, acc["ema"])
    else:   # EMA de la temporalidad superior, solo con velas ya cerradas
        hE = ema([b["c"] for b in htf_bars], acc["ema"]); hms = TF_MS[acc["htf"]]
        hclose = [b["t"] + hms for b in htf_bars]
        T = []
        for b in bars:
            j = bisect.bisect_right(hclose, b["t"]) - 1
            T.append(hE[j] if j >= 0 else float("nan"))
    R = rsi(closes, acc["rsi"]) if acc["fam"] == "PB" else None
    SN = None
    if acc.get("sniper"):   # tendencia SMA 9 / SMA 21 de H1 (solo velas cerradas), como strategy/sniper.py
        hc = [b["c"] for b in htf_bars]
        def sma(n, k): return sum(hc[k - n + 1:k + 1]) / n if k >= n - 1 else float("nan")
        sig = [(1 if sma(9, k) > sma(21, k) else -1 if sma(9, k) < sma(21, k) else 0) if k >= 20 else 0 for k in range(len(hc))]
        hms = TF_MS[acc["htf"]]; hclose = [b["t"] + hms for b in htf_bars]
        SN = []
        for b in bars:
            j = bisect.bisect_right(hclose, b["t"]) - 1
            SN.append(sig[j] if j >= 0 else 0)
    return T, A, R, SN

def process(acc, st, fx):
    if acc["fam"] == "SMC": return process_smc(acc, st, fx)
    if acc["fam"] == "SMC_AGENT": return process_smc_agent(acc, st, fx)
    t = now_utc(); nowms = time.time() * 1000
    st["status"] = "abierto" if gold_open(t) else "cerrado"
    tfms = TF_MS[acc["tf"]]
    bars = closed_bars(acc["tf"], 1000)
    htf_bars = closed_bars(acc["htf"], 1000) if acc["fam"] != "BO" and acc.get("htf") not in (None, "same") else []
    need = (acc["ema"] if acc["fam"] == "BO" or acc.get("htf") == "same" else 30) + acc.get("don", 0) + 20
    if len(bars) < need: return
    T, A, R, SN = build_indicators(acc, bars, htf_bars)
    pos_of = {b["t"]: i for i, b in enumerate(bars)}

    def snapshot(i):
        b = bars[i]
        ind = dict(bar_time=ms_to_dt(b["t"]).isoformat(), close=b["c"], trend_line=round(T[i], 2), atr=round(A[i], 2),
                   stop_dist=round(acc["mult"] * A[i], 2), trend="alcista" if b["c"] > T[i] else "bajista")
        if acc["fam"] in ("BO", "BOH"):
            ind["don_hi"] = round(max(x["h"] for x in bars[i - acc["don"]:i]), 2)
            ind["don_lo"] = round(min(x["l"] for x in bars[i - acc["don"]:i]), 2)
        else:
            ind["rsi"] = round(R[i], 1)
        st["ind"] = ind
        return ind

    if st["last_sig_bar"] is None:
        st["last_sig_bar"] = bars[-1]["t"]
        st["last_1m"] = int(nowms // 60_000 * 60_000) - 60_000
        snapshot(len(bars) - 1)
        event(st, "Arranque: cuenta de 1000 € lista. Se opera desde la próxima vela cerrada.")

    def on_bar_close(i, fill_px, fill_time):
        b = bars[i]; dist = acc["mult"] * A[i]; ind = snapshot(i)
        if dist <= 0 or T[i] != T[i]: return
        in_sess = acc["sess"][0] <= ms_to_dt(b["t"]).hour < acc["sess"][1]
        if acc["fam"] in ("BO", "BOH"):
            if st["pos"]:
                ns = b["c"] - st["pos"] * dist
                st["stop"] = max(st["stop"], ns) if st["pos"] == 1 else min(st["stop"], ns)
            if not in_sess or len(st["units"]) >= acc["units"]: return
            if acc.get("news") and news_block(b["t"]): return
            if SN is not None and SN[i] == 0: return
            if b["c"] > ind["don_hi"] and b["c"] > T[i] and st["pos"] >= 0 and (SN is None or SN[i] > 0):
                open_unit(acc, st, 1, fill_px, dist, fill_time, fx)
            elif acc["shorts"] and b["c"] < ind["don_lo"] and b["c"] < T[i] and st["pos"] <= 0 and (SN is None or SN[i] < 0):
                open_unit(acc, st, -1, fill_px, dist, fill_time, fx)
        else:  # PB
            if st["pos"] == 1 and R[i] > acc["hi"]:
                close_position(acc, st, fill_px, fill_time, f"RSI {R[i]:.0f} > {acc['hi']}", fx); return
            if st["pos"] == -1 and R[i] < 100 - acc["hi"]:
                close_position(acc, st, fill_px, fill_time, f"RSI {R[i]:.0f} < {100 - acc['hi']}", fx); return
            if st["pos"] == 0 and in_sess:
                if b["c"] > T[i] and R[i] < acc["lo"]:
                    open_unit(acc, st, 1, fill_px, dist, fill_time, fx)
                elif acc["shorts"] and b["c"] < T[i] and R[i] > 100 - acc["lo"]:
                    open_unit(acc, st, -1, fill_px, dist, fill_time, fx)

    start = st["last_1m"] + 60_000
    while start < nowms - 60_000:
        m1 = closed_bars("1m", 1000, start)
        if not m1:
            start += 1000 * 60_000; continue
        for b in m1:
            bt = ms_to_dt(b["t"])
            for tb in [x["t"] for x in bars if x["t"] > st["last_sig_bar"] and x["t"] + tfms <= b["t"]]:
                on_bar_close(pos_of[tb], b["o"], bt.isoformat())
                st["last_sig_bar"] = tb
            day = trading_day(bt)
            if st["units"] and st["last_swap_day"] and day != st["last_swap_day"] and st["pos"] == 1:
                nights = 3 if datetime.fromisoformat(day).weekday() == 3 else 1   # rollover del miércoles
                sw = sum(q for q, _, _ in st["units"]) * b["o"] * SWAP_LONG / 365 * nights / fx
                st["cash"] -= sw; st["swaps_paid"] += sw
                if st["trade_open"]: st["trade_open"]["swaps"] += sw
                event(st, f"Swap cobrado: {sw:,.2f} € ({nights} noche{'s' if nights > 1 else ''})")
            st["last_swap_day"] = day
            if st["units"]:
                hit = b["l"] <= st["stop"] if st["pos"] == 1 else b["h"] >= st["stop"]
                if hit:
                    px = min(b["o"], st["stop"]) if st["pos"] == 1 else max(b["o"], st["stop"])
                    close_position(acc, st, px, bt.isoformat(), "stop", fx)
            st["last_1m"] = b["t"]; st["price"] = b["c"]
        start = st["last_1m"] + 60_000
    if gold_open(t):
        try:
            lp = last_price()
            if lp: st["price"] = lp
        except Exception: pass
    px = st["price"]
    if px or not st["units"]:
        st["equity"] = equity_eur(st, px or 0.0, fx)
        st["peak"] = max(st["peak"], st["equity"])
        st["max_dd"] = min(st["max_dd"], st["equity"] / st["peak"] - 1)
        if not st["curve"] or nowms - st["curve"][-1][0] >= 5 * 60_000:
            st["curve"].append([int(nowms), round(st["equity"], 2)])
            if len(st["curve"]) > 6000: st["curve"] = st["curve"][::2]

# ---------- cuenta SMC mecánica: repite el backtest exacto (strategy/smc.py) sobre las velas guardadas ----------
SMC_WARMUP = 1000   # velas M15 de calentamiento (~2 semanas) para estructura, sesgo H4 y EMA 200 de H1

def process_smc(acc, st, fx):
    """Las velas cerradas se guardan en el estado (no cambian después) y en cada pasada se simula de nuevo desde el
    arranque de la cuenta con smc.simulate: las operaciones son exactamente las del backtest con esos datos.
    Stops y objetivos se evalúan con el máximo/mínimo de cada vela M15 al cerrarse (como en el backtest)."""
    import pandas as pd
    if str(HERE / "strategy") not in sys.path: sys.path.insert(0, str(HERE / "strategy"))
    import smc
    t = now_utc(); tfms = TF_MS[acc["tf"]]
    st["status"] = "abierto" if gold_open(t) else "cerrado"
    store = st.setdefault("bars", [])
    last = store[-1][0] if store else -1
    for b in closed_bars(acc["tf"], SMC_WARMUP):
        if b["t"] > last: store.append([b["t"], round(b["o"], 2), round(b["h"], 2), round(b["l"], 2), round(b["c"], 2)])
    if not store: return
    if st.get("start_ms") is None:
        st["start_ms"] = store[-1][0] + tfms
        event(st, "Arranque: cuenta de 1000 € lista. Se opera desde la próxima vela M15 cerrada.")
    k0 = next((k for k, b in enumerate(store) if b[0] >= st["start_ms"]), len(store))
    if k0 > SMC_WARMUP: del store[:k0 - SMC_WARMUP]                    # solo el calentamiento necesario
    df = pd.DataFrame(store, columns=["t", "Open", "High", "Low", "Close"])
    df.index = pd.to_datetime(df.pop("t"), unit="ms", utc=True)
    i0 = int(df.index.searchsorted(pd.Timestamp(st["start_ms"], unit="ms", tz="UTC")))
    p = acc["smc"]
    if gold_open(t):
        try: st["price"] = last_price()
        except Exception: pass
    st["price"] = st.get("price") or float(df.Close.iloc[-1])
    if i0 >= len(df):
        st["equity"] = CAPITAL_EUR; return
    pre = smc.prepare(df, p); cv, tr, so, sg = [], [], {}, []
    r = smc.simulate(df, i0, p, pre=pre, cost_side=COST_SIDE, risk=acc["risk"], max_risk=acc["risk"],
                     curve=cv, trades_out=tr, state_out=so, signals_out=sg)
    # registro de decisiones: cada setup SMC confirmado, con lo que se hizo y por qué (también lo usarán los agentes)
    st["setups_total"] = len(sg); st["setups_taken"] = sum(1 for x in sg if x["result"] == "operado")
    st["setups"] = [dict(time=x["time"].isoformat(), side="LARGO" if x["side"] == 1 else "CORTO",
                         zone=[round(x["zone_lo"], 2), round(x["zone_hi"], 2)], stop=round(x["stop"], 2),
                         close=round(x["close"], 2), result=x["result"]) for x in sg[-100:]][::-1]
    E = smc.EURUSD   # el backtest convierte a € con EURUSD fijo; la cuenta gemela usa lo mismo para ser idéntica
    seen = st.get("seen", 0)
    for x in tr[seen:]:
        event(st, f"CIERRE {'largo' if x['side'] == 1 else 'corto'} {x['qty']:g} oz a {x['exit']:,.2f} ({x['reason']}) · "
                  f"{x['pnl_eur']:+,.2f} € ({x['R']:+.2f} R)")
    st["seen"] = len(tr)
    st["trades"] = [dict(side="LARGO" if x["side"] == 1 else "CORTO", entry_time=x["t_in"].isoformat(),
                         exit_time=x["t_out"].isoformat(), units=1, qty=x["qty"], avg_entry=round(x["entry"], 2),
                         exit=round(x["exit"], 2), pnl=round(x["pnl_eur"], 2), R=round(x["R"], 2), reason=x["reason"])
                    for x in reversed(tr)]
    if so["pos"]:
        sig = f"{so['pos']}@{so['t_open']}"
        if st.get("open_sig") != sig:
            q = sum(qq for qq, _ in so["parts"])
            event(st, f"{'COMPRA' if so['pos'] == 1 else 'VENTA'}: {q:g} oz a {so['entry']:,.2f} · stop {so['stop']:,.2f} · "
                      f"objetivos {', '.join(f'{tg:,.2f}' for _, tg in so['parts'])} · riesgo {so['risk_usd'] / E:,.0f} €")
            st["open_sig"] = sig
        elif so["be"] and not st.get("be_note"):
            event(st, f"TP1 alcanzado: stop a break even ({so['entry']:,.2f})"); st["be_note"] = True
    else:
        st["open_sig"] = None; st["be_note"] = False
    st["pos"] = so["pos"]; st["stop"] = so["stop"] if so["pos"] else None
    st["units"] = [[qq, so["entry"], str(so["t_open"])] for qq, _ in so["parts"]] if so["pos"] else []
    st["cash"] = so["eq"] / E
    st["equity"] = st["cash"] + (sum(so["pos"] * qq * (st["price"] - so["entry"]) for qq, _ in so["parts"]) / E if so["pos"] else 0.0)
    curve_eur = [v for v in cv] + [st["equity"]]
    pk = CAPITAL_EUR; dd = 0.0
    for v in curve_eur: pk = max(pk, v); dd = min(dd, v / pk - 1)
    st["peak"] = pk; st["max_dd"] = dd; st["skipped"] = r["skipped"]
    nz = sum(1 for z in pre["zones"] if 0 <= len(df) - 1 - z["born"] <= p["age"])
    st["ind"] = dict(bar_time=df.index[-1].isoformat(), close=float(df.Close.iloc[-1]),
                     bias={1: "alcista", -1: "bajista"}.get(int(pre["bias"][-1]), "neutral"),
                     ema={1: "precio encima", -1: "precio debajo"}.get(int(pre["ema"][-1]) if pre["ema"] is not None else 0, "—"),
                     zones=nz, targets=[tg for _, tg in so["parts"]] if so["pos"] else [])
    nowms = time.time() * 1000
    if not st["curve"] or nowms - st["curve"][-1][0] >= 5 * 60_000:
        st["curve"].append([int(nowms), round(st["equity"], 2)])
        if len(st["curve"]) > 6000: st["curve"] = st["curve"][::2]

# ---------- cuenta SMC de agentes (fase 2: agente de entrada) ----------
ACC_STATES = {}
NON_FILTERS = ("posición abierta", "fin de datos")   # motivos de descarte que dependen de la gemela, no del setup

def _bars_table(df, n):
    x = df.tail(n)
    return [[t.strftime("%m-%d %H:%M"), round(r.Open, 2), round(r.High, 2), round(r.Low, 2), round(r.Close, 2)]
            for t, r in x.iterrows()]

def agent_context(df, pre, sig, fails, st, E, price):
    import pandas as pd
    h1 = df.resample("1h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    h4 = df.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    e200 = h1.Close.ewm(span=200, adjust=False).mean().iloc[-1]
    s9, s21 = h1.Close.rolling(9).mean().iloc[-1], h1.Close.rolling(21).mean().iloc[-1]
    t = df.index[-1] + pd.Timedelta(minutes=15)
    eq = st["eq_usd"] / E
    last = [dict(lado=x["side"], R=x["R"], motivo=x["reason"], salida=iso_madrid(x["exit_time"])) for x in st["trades"][:5]]
    return dict(
        setup=dict(lado="LARGO" if sig["side"] == 1 else "CORTO", zona=[round(sig["zone_lo"], 2), round(sig["zone_hi"], 2)],
                   stop_mecanico=round(sig["stop"], 2), cierre=round(sig["close"], 2), atr_m15=round(sig["atr"], 2),
                   filtros_que_fallan=fails, la_gemela_lo_opera=not fails,
                   objetivos_gemela_R=[2, 3, 4]),
        contexto=dict(hora_utc=t.strftime("%Y-%m-%d %H:%M"), hora_madrid=t.tz_convert(MAD).strftime("%H:%M"),
                      hora_nueva_york=t.tz_convert(NY).strftime("%H:%M %a"),
                      sesgo_estructura_H4={1: "alcista", -1: "bajista"}.get(int(pre["bias"][-1]), "neutral"),
                      ema200_H1=round(float(e200), 2), sma9_H1=round(float(s9), 2), sma21_H1=round(float(s21), 2),
                      precio_ahora=round(float(price), 2)),
        velas=dict(M15_ultimas_48=_bars_table(df, 48), H1_ultimas_48=_bars_table(h1, 48), H4_ultimas_30=_bars_table(h4, 30),
                   formato="[fecha UTC, apertura, máximo, mínimo, cierre]"),
        cuenta=dict(capital_eur=round(eq, 2), resultado_hoy_pct=round((st["eq_usd"] / st["day_start"] - 1) * 100, 2),
                    perdidas_hoy=st["day_losses"], ultimas_operaciones=last),
    )

def mgmt_context(df, st, C):
    import pandas as pd
    h1 = df.resample("1h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    d = st["pos"]; dist0 = st.get("dist0") or 1.0
    t = df.index[-1] + pd.Timedelta(minutes=15)
    nb = news_block(int(t.timestamp() * 1000)) or news_block(int((t + pd.Timedelta(minutes=15)).timestamp() * 1000))
    return dict(
        posicion=dict(lado="LARGO" if d == 1 else "CORTO", entrada=round(st["entry"], 2), stop=round(st["stop"], 2),
                      break_even_hecho=bool(st["be_done"]), objetivos=[round(v, 2) for _, v in st["parts"]],
                      oz_por_objetivo=[q for q, _ in st["parts"]], R_ahora=round(d * (C - st["entry"]) / dist0, 2),
                      R_max=round(st.get("r_max", 0.0), 2), R_min=round(st.get("r_min", 0.0), 2),
                      velas_en_operacion=st.get("bars_in", 0), motivo_de_entrada=st.get("entry_reason", ""),
                      resultado_abierto_eur=round(d * sum(q for q, _ in st["parts"]) * (C - st["entry"]) / 1.12, 2)),
        contexto=dict(hora_utc=t.strftime("%Y-%m-%d %H:%M"), hora_nueva_york=t.tz_convert(NY).strftime("%H:%M %a"),
                      precio_ahora=round(float(C), 2), ventana_de_noticias_en_15_30_min=bool(nb),
                      ema200_H1=round(float(h1.Close.ewm(span=200, adjust=False).mean().iloc[-1]), 2)),
        velas=dict(M15_ultimas_32=_bars_table(df, 32), H1_ultimas_24=_bars_table(h1, 24),
                   formato="[fecha UTC, apertura, máximo, mínimo, cierre]"),
    )

def process_smc_agent(acc, st, fx):
    import pandas as pd
    if str(HERE / "strategy") not in sys.path: sys.path.insert(0, str(HERE / "strategy"))
    import smc, risk_guard, agents
    t = now_utc(); tfms = TF_MS[acc["tf"]]; E = smc.EURUSD; p = acc["smc"]
    st["status"] = "abierto" if gold_open(t) else "cerrado"
    store = (ACC_STATES.get("gold_smc_mec") or {}).get("bars") or []
    if not store: return
    if st.get("start_ms") is None:
        st.update(start_ms=store[-1][0] + tfms, last_bar=store[-1][0], last_setup=None, eq_usd=CAPITAL_EUR * E,
                  day=None, day_start=CAPITAL_EUR * E, day_losses=0, parts=[], be=True, be_done=False, entry=0.0,
                  risk_usd=0.0, trade_pnl=0.0, t_open=None, pend=None, decisions=[], spend={})
        event(st, "Arranque: cuenta de 1000 € lista. El agente decide desde el próximo setup SMC.")
    if gold_open(t):
        try: st["price"] = last_price()
        except Exception: pass
    st["price"] = st.get("price") or store[-1][4]

    def close_trade(px, when, reason):
        pos = st["pos"]
        for q, _ in st["parts"]:
            pnl = pos * q * (px - pos * COST_SIDE - st["entry"]); st["eq_usd"] += pnl; st["trade_pnl"] += pnl
        tp = st["trade_pnl"]
        if tp <= 0: st["day_losses"] += 1
        st["trades"].insert(0, dict(side="LARGO" if pos == 1 else "CORTO", entry_time=st["t_open"], exit_time=when, units=1,
                                    qty=st["qty"], avg_entry=round(st["entry"], 2), exit=round(px, 2), pnl=round(tp / E, 2),
                                    R=round(tp / st["risk_usd"], 2) if st["risk_usd"] else 0.0, reason=reason))
        event(st, f"CIERRE {'largo' if pos == 1 else 'corto'} a {px:,.2f} ({reason}) · {tp / E:+,.2f} €")
        st.update(pos=0, parts=[], units=[], stop=None, trade_pnl=0.0, be_done=False)

    # 1) velas nuevas: entrada pendiente, swap, stop y objetivos (igual que el backtest)
    for bt, O, H, L, C in store:
        if bt <= st["last_bar"] or bt < st["start_ms"]: continue
        when = ms_to_dt(bt).isoformat()
        dday = ms_to_dt(bt) - timedelta(hours=22)
        if dday.date().isoformat() != st["day"]:
            if st["day"] is not None and st["pos"] == 1:
                sw = sum(q for q, _ in st["parts"]) * O * SWAP_LONG / 365 * (3 if dday.weekday() == 2 else 1)
                st["eq_usd"] -= sw; st["trade_pnl"] -= sw; st["swaps_paid"] += sw / E
            st["day"] = dday.date().isoformat(); st["day_start"] = st["eq_usd"]; st["day_losses"] = 0
        pend = st.get("pend")
        if pend and not st["pos"]:
            st["pend"] = None; d = pend["side"]; px = O + d * COST_SIDE; dist = (px - pend["stop"]) * d
            rr = [(tg - px) * d / dist for tg in pend["targets"]] if dist > 0 else []
            if dist <= 0 or not rr or min(rr) <= 0: q, why = 0.0, "stop u objetivo al lado incorrecto tras la apertura"
            else:
                q, why = risk_guard.size_entry(st["eq_usd"], st["day_start"], st["day_losses"], px, dist, risk=acc["risk"],
                                               max_risk=acc["risk"], targets_r=rr)
            rec = next((x for x in st["decisions"] if x.get("id") == pend["id"]), None)
            if q >= 1:
                tg = pend["targets"]; n = int(min(len(tg), q)); base = q // n; rest = q - base * n
                sel = {1: [len(tg) // 2], 2: [0, len(tg) - 1]}.get(n, list(range(len(tg))))
                st["parts"] = [[base + (1 if k < rest else 0), tg[ti]] for k, ti in enumerate(sel)]
                st.update(pos=d, entry=px, stop=pend["stop"], be=pend["be"], be_done=False, risk_usd=q * dist, qty=q,
                          t_open=when, trade_pnl=0.0, dist0=dist, bars_in=0, r_max=0.0, r_min=0.0, pend_mgmt=None,
                          entry_reason=(rec or {}).get("reason", ""))
                st["costs_paid"] += q * COST_SIDE * 2 / E
                event(st, f"{'COMPRA' if d == 1 else 'VENTA'} (agente): {q:g} oz a {px:,.2f} · stop {pend['stop']:,.2f} · "
                          f"objetivos {', '.join(f'{x:,.2f}' for _, x in st['parts'])} · riesgo {q * dist / E:,.0f} €")
                if rec: rec["resultado"] = f"operado: {q:g} oz a {px:,.2f}"
            else:
                event(st, f"Entrada del agente rechazada por el guardián: {why}")
                if rec: rec["resultado"] = f"rechazado por el guardián: {why}"
        pm = st.get("pend_mgmt")
        if pm and st["pos"]:
            st["pend_mgmt"] = None; d = st["pos"]
            if pm == "cerrar": close_trade(O, when, "cierre del gestor")
            elif pm == "cerrar_parcial" and st["parts"]:
                k = min(range(len(st["parts"])), key=lambda j: abs(st["parts"][j][1] - O))
                q, _ = st["parts"].pop(k)
                pnl = d * q * (O - d * COST_SIDE - st["entry"]); st["eq_usd"] += pnl; st["trade_pnl"] += pnl
                event(st, f"Cierre parcial del gestor: {q:g} oz a {O:,.2f} · {pnl / E:+,.2f} €")
                if not st["parts"]: close_trade(O, when, "cierre del gestor")
        if st["pos"]:
            d = st["pos"]
            st["bars_in"] = st.get("bars_in", 0) + 1
            if st.get("dist0"):
                st["r_max"] = max(st.get("r_max", 0.0), d * ((H if d == 1 else L) - st["entry"]) / st["dist0"])
                st["r_min"] = min(st.get("r_min", 0.0), d * ((L if d == 1 else H) - st["entry"]) / st["dist0"])
            if (L <= st["stop"]) if d == 1 else (H >= st["stop"]):
                close_trade(min(O, st["stop"]) if d == 1 else max(O, st["stop"]), when, "BE" if st["be_done"] else "stop")
            else:
                keep = []
                for q, tg in st["parts"]:
                    if (d == 1 and H >= tg) or (d == -1 and L <= tg):
                        pnl = d * q * (tg - d * COST_SIDE - st["entry"]); st["eq_usd"] += pnl; st["trade_pnl"] += pnl
                        event(st, f"Objetivo {tg:,.2f} alcanzado ({q:g} oz)")
                        if st["be"] and not st["be_done"]: st["stop"] = st["entry"]; st["be_done"] = True
                    else: keep.append([q, tg])
                st["parts"] = keep
                if not keep: close_trade(C, when, "objetivos")   # sin partes abiertas: solo registra la operación
        st["last_bar"] = bt

    # 2) setups confirmados en la última vela cerrada -> el agente decide
    df = pd.DataFrame(store, columns=["t", "Open", "High", "Low", "Close"])
    df.index = pd.to_datetime(df.pop("t"), unit="ms", utc=True)
    i0 = int(df.index.searchsorted(pd.Timestamp(st["start_ms"], unit="ms", tz="UTC")))
    if i0 < len(df):
        pre = smc.prepare(df, p); sg = []
        smc.simulate(df, i0, p, pre=pre, cost_side=COST_SIDE, risk=acc["risk"], max_risk=acc["risk"], signals_out=sg)
        last_t = df.index[-1]; today = t.date().isoformat()
        for x in sg:
            if st["last_setup"] and x["time"].isoformat() <= st["last_setup"]: continue
            st["last_setup"] = x["time"].isoformat()
            res = x["result"]
            fails = [] if res in ("operado", "pendiente") else [f for f in res.split(": ", 1)[-1].split(", ") if f not in NON_FILTERS]
            rec = dict(id=f"{x['time'].isoformat()}{x['side']}", time=x["time"].isoformat(), side="LARGO" if x["side"] == 1 else "CORTO",
                       zone=[round(x["zone_lo"], 2), round(x["zone_hi"], 2)], stop_mec=round(x["stop"], 2),
                       gemela="opera" if not fails else "descarta: " + ", ".join(fails))
            if x["time"] != last_t: rec["agente"] = "no evaluado: setup antiguo (retraso del motor)"
            elif "noticia" in fails: rec["agente"] = "bloqueado: ventana de noticias"
            elif st["pos"] or st.get("pend"): rec["agente"] = "no evaluado: ya hay una posición abierta"
            elif st["day_losses"] >= 4: rec["agente"] = "no evaluado: día parado (4 pérdidas)"
            else:
                ctx = agent_context(df, pre, x, fails, st, E, st["price"])
                r = agents.decide_entry(ctx, st["spend"].get(today, 0.0))
                st["spend"][today] = round(st["spend"].get(today, 0.0) + r["cost"], 4)
                rec.update(agente=r["decision"] if not r["error"] else f"error: {r['error']}", stop=r.get("stop"),
                           targets=r.get("targets"), be=r.get("break_even"), conf=r.get("confidence"),
                           reason=r.get("reason", ""), cost=round(r["cost"], 4), model=r["model"], ms=r.get("ms"))
                d = x["side"]
                if not r["error"] and r["decision"] == "entrar":
                    tg = sorted(r["targets"], reverse=(d == -1))[:3]
                    if r["stop"] and ((r["stop"] < x["close"]) if d == 1 else (r["stop"] > x["close"])) and tg:
                        st["pend"] = dict(side=d, stop=float(r["stop"]), targets=[float(v) for v in tg],
                                          be=bool(r["break_even"]), id=rec["id"])
                        rec["resultado"] = "pendiente de la apertura de la siguiente vela"
                    else: rec["resultado"] = "rechazado: stop u objetivos al lado incorrecto"
                event(st, f"Setup {rec['side'].lower()} · agente: {rec['agente']} · {r['cost']:.3f} $")
            st["decisions"].insert(0, rec)
        del st["decisions"][200:]
    # 3) fase 3: el gestor revisa la operación abierta al cierre de cada vela M15 (solo la vela recién cerrada)
    last_bt = store[-1][0]
    fresh = time.time() * 1000 - (last_bt + tfms) < 20 * 60_000
    if st["pos"] and st.get("last_review") != last_bt and st["last_bar"] == last_bt and fresh and not st.get("pend_mgmt"):
        st["last_review"] = last_bt; today = t.date().isoformat()
        C = store[-1][4]; d = st["pos"]
        ctx = mgmt_context(df, st, C)
        r = agents.decide_manage(ctx, st["spend"].get(today, 0.0))
        st["spend"][today] = round(st["spend"].get(today, 0.0) + r["cost"], 4)
        acc_ = r["accion"]; res = "sin cambios"
        if r["error"]: acc_, res = "mantener", f"error: {r['error']}"
        elif acc_ in ("mover_stop", "break_even"):
            new = st["entry"] if acc_ == "break_even" else float(r["nuevo_stop"])
            ok, why = risk_guard.check_stop_change(d, st["stop"], new, C)
            if ok:
                st["stop"] = new; st["be_done"] = st["be_done"] or abs(new - st["entry"]) < 1e-6 or (new - st["entry"]) * d > 0
                res = f"stop a {new:,.2f}"; event(st, f"Gestor: stop a {new:,.2f}")
            else: res = f"rechazado por el guardián: {why}"
        elif acc_ == "cambiar_objetivos":
            tg = [float(v) for v in r["nuevos_objetivos"]]
            if len(tg) == len(st["parts"]) and all((v - C) * d > 0 for v in tg):
                tg = sorted(tg, reverse=(d == -1))
                st["parts"] = [[q, v] for (q, _), v in zip(st["parts"], tg)]
                res = "objetivos: " + ", ".join(f"{v:,.2f}" for v in tg); event(st, f"Gestor: {res}")
            else: res = "rechazado: número de objetivos distinto o al lado incorrecto"
        elif acc_ in ("cerrar", "cerrar_parcial"):
            st["pend_mgmt"] = acc_; res = "a la apertura de la siguiente vela"
            event(st, f"Gestor: {'cerrar todo' if acc_ == 'cerrar' else 'cierre parcial'} en la siguiente vela")
        st.setdefault("mgmt", []).insert(0, dict(time=ms_to_dt(last_bt).isoformat(), accion=acc_, resultado=res,
                                                 R=round(ctx["posicion"]["R_ahora"], 2), reason=r.get("reason", ""),
                                                 cost=round(r["cost"], 4), model=r["model"]))
        del st["mgmt"][300:]
    # 4) capital y curva
    st["units"] = [[q, st["entry"], st["t_open"]] for q, _ in st["parts"]] if st["pos"] else []
    st["cash"] = st["eq_usd"] / E
    st["equity"] = st["cash"] + (sum(st["pos"] * q * (st["price"] - st["entry"]) for q, _ in st["parts"]) / E if st["pos"] else 0.0)
    st["peak"] = max(st["peak"], st["equity"]); st["max_dd"] = min(st["max_dd"], st["equity"] / st["peak"] - 1)
    st["setups_total"] = len(st["decisions"]); st["setups_taken"] = sum(1 for x in st["decisions"] if str(x.get("resultado", "")).startswith("operado"))
    st["ind"] = dict(spend_today=st["spend"].get(t.date().isoformat(), 0.0), spend_total=round(sum(st["spend"].values()), 4),
                     targets=[tg for _, tg in st["parts"]] if st["pos"] else [], cap=agents.DAILY_CAP_USD)
    nowms = time.time() * 1000
    if not st["curve"] or nowms - st["curve"][-1][0] >= 5 * 60_000:
        st["curve"].append([int(nowms), round(st["equity"], 2)])
        if len(st["curve"]) > 6000: st["curve"] = st["curve"][::2]

# ---------- dashboard ----------
def spark(curve, w=520, h=120):
    if len(curve) < 2:
        return f'<svg viewBox="0 0 {w} {h}" class="chart"><text x="10" y="{h/2}" class="muted">Recogiendo datos…</text></svg>'
    xs = [p[0] for p in curve]; ys = [p[1] for p in curve]
    x0, x1 = xs[0], xs[-1]; lo, hi = min(ys + [CAPITAL_EUR]), max(ys + [CAPITAL_EUR])
    if hi - lo < 1: hi, lo = hi + 1, lo - 1
    fx_ = lambda x: 4 + (x - x0) / max(1, x1 - x0) * (w - 8)
    fy = lambda y: h - 6 - (y - lo) / (hi - lo) * (h - 12)
    pts = " ".join(f"{fx_(x):.1f},{fy(y):.1f}" for x, y in curve)
    col = "#3fb68b" if ys[-1] >= CAPITAL_EUR else "#e5534b"
    return (f'<svg viewBox="0 0 {w} {h}" class="chart"><line x1="0" x2="{w}" y1="{fy(CAPITAL_EUR):.1f}" y2="{fy(CAPITAL_EUR):.1f}" class="base"/>'
            f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2"/></svg>')

# Colores por cuenta (paleta categórica para fondo oscuro; el color sigue a la cuenta, no a su posición)
SERIES_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9", "#e66767", "#008300"]
def acc_color(i): return SERIES_COLORS[i % len(SERIES_COLORS)]

SMC_EVAL_TRADES = 100   # criterio fijado: evaluar el experimento con >= 100 operaciones o 3 meses
SMC_EVAL_DAYS = 91

def smc_stats(st):
    tr = st.get("trades", []); n = len(tr); wins = [x for x in tr if x["pnl"] > 0]
    gp = sum(x["pnl"] for x in wins); gl = -sum(x["pnl"] for x in tr if x["pnl"] <= 0)
    return dict(n=n, wr=f"{len(wins) / n * 100:.0f} %" if n else "—", pf=f"{gp / gl:.2f}" if gl > 0 else ("∞" if gp > 0 else "—"),
                r=f"{sum(x['R'] for x in tr) / n:+.2f}" if n else "—", pl=st.get("equity", CAPITAL_EUR) - CAPITAL_EUR,
                dd=st.get("max_dd", 0.0), setups=st.get("setups_total", 0), taken=st.get("setups_taken", 0))

def smc_section(s):
    """Experimento: equipo de agentes SMC frente a la gemela mecánica (mismos setups, mismo riesgo)."""
    mec = s["accounts"].get("gold_smc_mec")
    if not mec: return ""
    ag = s["accounts"].get("gold_smc_agents")
    a, b = smc_stats(mec), (smc_stats(ag) if ag else None)
    def col(x, k, f=lambda v: v): return f(x[k]) if x else '<span class="muted">fase 2</span>'
    rows = [("Operaciones", "n", str), ("Acierto", "wr", str), ("PF", "pf", str), ("R medio por operación", "r", str),
            ("Resultado", "pl", lambda v: f'<span class="{"up" if v > 0 else "dn" if v < 0 else ""}">{v:+,.2f} €</span>'),
            ("Caída máx.", "dd", lambda v: f"{v * 100:.2f} %"), ("Setups detectados / operados", "setups", None)]
    body = ""
    for name, k, f in rows:
        if k == "setups":
            bt = f'{b["setups"]} / {b["taken"]}' if b else '<span class="muted">fase 2</span>'
            body += f'<tr><td>{name}</td><td>{a["setups"]} / {a["taken"]}</td><td>{bt}</td></tr>'
        else:
            body += f'<tr><td>{name}</td><td>{f(a[k])}</td><td>{col(b, k, f)}</td></tr>'
    start = mec.get("start_ms"); days = (time.time() * 1000 - start) / 86_400_000 if start else 0
    prog = (f'Progreso hacia la evaluación: <b>{a["n"]}</b> de {SMC_EVAL_TRADES} operaciones · <b>{days:.0f}</b> de {SMC_EVAL_DAYS} días '
            f'(lo que llegue antes). Criterio fijado: la cuenta de agentes debe ganar a la mecánica y a entradas al azar con la misma gestión.')
    if ag:
        body += (f'<tr><td>Gasto de la API (hoy / total)</td><td class="muted">—</td>'
                 f'<td>{ag.get("ind", {}).get("spend_today", 0):.2f} $ / {ag.get("ind", {}).get("spend_total", 0):.2f} $</td></tr>')
    decs = (ag or {}).get("decisions", [])[:15]
    drows = "".join(f'<tr><td>{iso_madrid(x["time"])}</td><td>{x["side"]}</td><td class="muted">{html.escape(x["gemela"])}</td>'
                    f'<td>{html.escape(str(x.get("agente", "")))}</td><td>{html.escape(str(x.get("resultado", "")))}</td>'
                    f'<td class="muted">{html.escape(str(x.get("reason", "")))}</td><td>{x.get("cost", 0) or 0:.3f} $</td></tr>'
                    for x in decs) or '<tr><td colspan="7" class="muted">El agente aún no ha evaluado ningún setup</td></tr>'
    mg = (ag or {}).get("mgmt", [])[:15]
    mrows = "".join(f'<tr><td>{iso_madrid(x["time"])}</td><td>{x["R"]:+.2f}</td><td>{html.escape(x["accion"])}</td>'
                    f'<td>{html.escape(x["resultado"])}</td><td class="muted">{html.escape(x.get("reason", ""))}</td><td>{x.get("cost", 0):.3f} $</td></tr>'
                    for x in mg) or '<tr><td colspan="6" class="muted">El gestor aún no ha revisado ninguna operación</td></tr>'
    setups = mec.get("setups", [])[:15]
    srows = "".join(f'<tr><td>{iso_madrid(x["time"])}</td><td>{x["side"]}</td><td>{fmt(x["zone"][0])} – {fmt(x["zone"][1])}</td>'
                    f'<td>{fmt(x["stop"])}</td><td class="{"up" if x["result"] == "operado" else "muted"}">{html.escape(x["result"])}</td></tr>'
                    for x in setups) or '<tr><td colspan="5" class="muted">Aún no se ha detectado ningún setup</td></tr>'
    return f'''<h2 style="margin-top:22px">Experimento SMC: agentes frente a mecánica</h2>
<div class="sub">Las dos cuentas ven los mismos setups SMC y pasan por el mismo guardián de riesgo (2 % por operación, −5 % diario,
4 pérdidas paran el día, ratio ≥ 2:1, el stop solo puede acercarse). La única diferencia será el juicio de los agentes.</div>
<div class="tablewrap"><table class="summary"><tr><th></th><th>SMC mecánica (control)</th><th>SMC agentes</th></tr>{body}</table></div>
<div class="sub" style="margin-top:6px">{prog}</div>
<details style="margin-top:8px"><summary>Registro de setups detectados (últimos 15, hora de Madrid)</summary>
<table><tr><th>Vela</th><th>Lado</th><th>Zona</th><th>Stop</th><th>Decisión</th></tr>{srows}</table></details>
<details style="margin-top:8px" open><summary>Decisiones del agente (últimas 15, hora de Madrid)</summary>
<div class="tablewrap"><table><tr><th>Vela</th><th>Lado</th><th>Gemela</th><th>Agente</th><th>Resultado</th><th>Motivo del agente</th><th>Coste</th></tr>{drows}</table></div></details>
<details style="margin-top:8px"><summary>Gestión de operaciones abiertas (últimas 15 revisiones, hora de Madrid)</summary>
<div class="tablewrap"><table><tr><th>Vela</th><th>R en ese momento</th><th>Acción</th><th>Resultado</th><th>Motivo del gestor</th><th>Coste</th></tr>{mrows}</table></div></details>'''

def summary_table(s, fx):
    rows = []
    best = max(ACCOUNTS, key=lambda a: s["accounts"][a["id"]]["equity"])["id"]
    for k, acc in enumerate(ACCOUNTS):
        st = s["accounts"][acc["id"]]; px = st.get("price")
        pl = st["equity"] - CAPITAL_EUR
        tr = st["trades"]; wins = [x for x in tr if x["pnl"] > 0]
        gp = sum(x["pnl"] for x in wins); gl = -sum(x["pnl"] for x in tr if x["pnl"] <= 0)
        pf = f"{gp / gl:.2f}" if gl > 0 else ("∞" if gp > 0 else "—")
        wr = f"{len(wins) / len(tr) * 100:.0f} %" if tr else "—"
        qty = sum(q for q, _, _ in st["units"])
        if qty:
            un = unreal_usd(st, px) / fx if px else 0.0
            pos = (f'<span class="tag {"long" if st["pos"] == 1 else "short"}">{"LARGO" if st["pos"] == 1 else "CORTO"} {qty:g} oz</span> '
                   f'<span class="{"up" if un >= 0 else "dn"}">{un:+,.2f} €</span>')
        else:
            pos = '<span class="muted">—</span>'
        lead = ' <span class="lead" title="Mayor capital ahora">▲ líder</span>' if acc["id"] == best and pl != 0 else ""
        rows.append(f'<tr><td><span class="key" style="background:{acc_color(k)}"></span>{html.escape(acc["name"])}{lead}</td>'
                    f'<td>{fmt(st["equity"])} €</td>'
                    f'<td class="{"up" if pl > 0 else "dn" if pl < 0 else ""}">{pl:+,.2f} € <span class="muted">({pl / CAPITAL_EUR * 100:+.2f} %)</span></td>'
                    f'<td>{len(tr)}</td><td>{wr}</td><td>{pf}</td><td>{st["max_dd"] * 100:.2f} %</td><td>{pos}</td></tr>')
    return ('<div class="tablewrap"><table class="summary"><tr><th>Cuenta</th><th>Capital</th><th>Resultado</th><th>Operaciones</th>'
            '<th>Acierto</th><th>PF</th><th>Caída máx.</th><th>Posición abierta</th></tr>' + "".join(rows) + "</table></div>")

def equity_chart(s):
    """Curvas de capital de todas las cuentas en un solo gráfico SVG con ejes, leyenda y tooltip."""
    series = []
    for k, acc in enumerate(ACCOUNTS):
        cv = s["accounts"][acc["id"]]["curve"]
        if cv: series.append(dict(name=acc["name"], color=acc_color(k), pts=cv))
    if not series or max(len(x["pts"]) for x in series) < 2:
        return '<div class="chartbox muted">Recogiendo datos para el gráfico…</div>'
    W, H, L, R, T, B = 1000, 320, 72, 16, 14, 34
    t0 = min(x["pts"][0][0] for x in series); t1 = max(x["pts"][-1][0] for x in series)
    if t1 - t0 < 3_600_000: t1 = t0 + 3_600_000
    ys = [p[1] for x in series for p in x["pts"]] + [CAPITAL_EUR]
    lo, hi = min(ys), max(ys)
    pad = max((hi - lo) * 0.08, 2.0); lo -= pad; hi += pad
    raw = (hi - lo) / 5; mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    ylo = math.floor(lo / step) * step; yhi = math.ceil(hi / step) * step
    X = lambda t: L + (t - t0) / (t1 - t0) * (W - L - R)
    Y = lambda v: T + (yhi - v) / (yhi - ylo) * (H - T - B)
    g = []
    v = ylo
    while v <= yhi + 1e-9:
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
                 f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" class="ax" text-anchor="end">{v:,.0f} €</text>')
        v += step
    g.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(CAPITAL_EUR):.1f}" y2="{Y(CAPITAL_EUR):.1f}" class="base"/>')
    span_h = (t1 - t0) / 3_600_000
    tick_h = next((h for h in (1, 2, 3, 6, 12, 24, 48, 72, 168, 336, 720) if span_h / h <= 8), 720)
    t = madrid(ms_to_dt(t0)).replace(minute=0, second=0, microsecond=0)
    if tick_h >= 24: t = t.replace(hour=0)
    while tick_h < 24 and t.hour % tick_h: t += timedelta(hours=1)
    while t.timestamp() * 1000 <= t1:
        ms = t.timestamp() * 1000
        if ms >= t0:
            lab = t.strftime("%d/%m") if tick_h >= 24 or t.hour == 0 else t.strftime("%H:%M")
            g.append(f'<line x1="{X(ms):.1f}" x2="{X(ms):.1f}" y1="{H - B}" y2="{H - B + 4}" class="tick"/>'
                     f'<text x="{X(ms):.1f}" y="{H - B + 18}" class="ax" text-anchor="middle">{lab}</text>')
        t = t + timedelta(hours=tick_h)
    lines = "".join(f'<polyline points="{" ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in x["pts"])}" fill="none" '
                    f'stroke="{x["color"]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' for x in series)
    legend = "".join(f'<span class="lg"><i style="background:{x["color"]}"></i>{html.escape(x["name"])}</span>' for x in series)
    data = json.dumps(dict(t0=t0, t1=t1, ylo=ylo, yhi=yhi, W=W, H=H, L=L, R=R, T=T, B=B,
                           s=[dict(n=x["name"], c=x["color"], p=x["pts"]) for x in series]))
    js = JS_CHART.replace("__DATA__", data)
    return (f'<div class="chartbox"><div class="legend">{legend}</div><div class="plot">'
            f'<svg id="eqc" viewBox="0 0 {W} {H}" role="img" aria-label="Capital de cada cuenta en euros a lo largo del tiempo">'
            f'{"".join(g)}{lines}<line id="xh" y1="{T}" y2="{H - B}" class="xh" style="display:none"/><g id="dots"></g></svg>'
            f'<div id="tip" class="tip" style="display:none"></div></div></div><script>{js}</script>')

JS_CHART = """(function(){
const D=__DATA__;const svg=document.getElementById("eqc"),tip=document.getElementById("tip"),xh=document.getElementById("xh"),dots=document.getElementById("dots");
const X=t=>D.L+(t-D.t0)/(D.t1-D.t0)*(D.W-D.L-D.R),Y=v=>D.T+(D.yhi-v)/(D.yhi-D.ylo)*(D.H-D.T-D.B);
const at=(p,t)=>{if(t<p[0][0])return null;let lo=0,hi=p.length-1;while(lo<hi){const m=(lo+hi+1)>>1;if(p[m][0]<=t)lo=m;else hi=m-1;}return p[lo];};
const nf=new Intl.NumberFormat("es-ES",{minimumFractionDigits:2,maximumFractionDigits:2});const ns="http://www.w3.org/2000/svg";
function show(ev){const r=svg.getBoundingClientRect();const sx=(ev.clientX-r.left)/r.width*D.W;
 const t=D.t0+(Math.min(Math.max(sx,D.L),D.W-D.R)-D.L)/(D.W-D.L-D.R)*(D.t1-D.t0);
 xh.setAttribute("x1",X(t));xh.setAttribute("x2",X(t));xh.style.display="";dots.replaceChildren();tip.replaceChildren();
 const h=document.createElement("div");h.className="th";h.textContent=new Date(t).toLocaleString("es-ES",{timeZone:"Europe/Madrid",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});tip.appendChild(h);
 const rows=D.s.map(s=>{const q=at(s.p,t);return q?{s:s,v:q[1]}:null;}).filter(Boolean).sort((a,b)=>b.v-a.v);
 for(const o of rows){const c=document.createElementNS(ns,"circle");c.setAttribute("cx",X(t));c.setAttribute("cy",Y(o.v));c.setAttribute("r",4);c.setAttribute("fill",o.s.c);c.setAttribute("stroke","#161c23");c.setAttribute("stroke-width",2);dots.appendChild(c);
  const d=document.createElement("div");d.className="tr";const k=document.createElement("i");k.style.background=o.s.c;const b=document.createElement("b");b.textContent=nf.format(o.v)+" €";const n=document.createElement("span");n.textContent=o.s.n;d.append(k,b,n);tip.appendChild(d);}
 tip.style.display="";const px=X(t)/D.W*r.width;tip.style.left=(px>r.width*0.55?px-tip.offsetWidth-12:px+12)+"px";}
function hide(){xh.style.display="none";tip.style.display="none";dots.replaceChildren();}
svg.addEventListener("pointermove",show);svg.addEventListener("pointerleave",hide);
})();"""

def fmt(v, d=2): return f"{v:,.{d}f}" if isinstance(v, (int, float)) else "—"

def render(s):
    cards = []; rows = []; fx = s.get("fx") or 1.12
    tot = sum(st["equity"] for st in s["accounts"].values()); tot0 = CAPITAL_EUR * len(ACCOUNTS)
    off = int(madrid(now_utc()).utcoffset().total_seconds() // 3600)
    oi = s.get("offset_info") or {}
    price_src = (f"{GOLD_SYMBOL} de Gate.io corregido al spot ({oi.get('src', '—')}): diferencia {-(s.get('offset') or 0):+.2f} $"
                 + (f" (medida {iso_madrid(oi['at'])})" if oi.get("at") else "")) if GOLD_API == "gate" else GOLD_SYMBOL
    sess_mad = f"{7 + off:02d}-{20 + off:02d} h en Madrid"
    for acc in ACCOUNTS:
        st = s["accounts"][acc["id"]]; ind = st.get("ind", {}); px = st.get("price")
        pl = st["equity"] - CAPITAL_EUR
        qty = sum(q for q, _, _ in st["units"])
        avg = sum(q * e for q, e, _ in st["units"]) / qty if qty else None
        un = unreal_usd(st, px) / fx if qty and px else 0.0
        if qty:
            lado = "LARGO" if st["pos"] == 1 else "CORTO"
            pos_html = (f'<div class="pos {"long" if st["pos"] == 1 else "short"}">{lado} · {len(st["units"])} unidad(es) · {qty:g} oz '
                        f'(= {qty / 100:.2f} lotes) · entrada media {fmt(avg)}<br>Stop: <b>{fmt(st["stop"])}</b> · '
                        f'P/L abierto: <b class="{"up" if un >= 0 else "dn"}">{un:+,.2f} €</b></div>')
        else:
            pos_html = '<div class="pos flat">Sin posición · esperando señal</div>'
        if acc["fam"] in ("BO", "BOH"):
            trig = (f'Compra si cierra &gt; <b>{fmt(ind.get("don_hi"))}</b>'
                    + (f' · venta si cierra &lt; <b>{fmt(ind.get("don_lo"))}</b>' if acc["shorts"] else ""))
        elif acc["fam"] == "SMC_AGENT":
            trig = (f'Setups evaluados: <b>{st.get("setups_total", 0)}</b> · operados: <b>{st.get("setups_taken", 0)}</b> · '
                    f'gasto de la API hoy <b>{ind.get("spend_today", 0):.2f} $</b> (tope {ind.get("cap", 0):.2f} $) · total <b>{ind.get("spend_total", 0):.2f} $</b>'
                    + (f' · objetivos: {", ".join(fmt(x) for x in ind.get("targets", []))}' if ind.get("targets") else ""))
        elif acc["fam"] == "SMC":
            trig = (f'Sesgo H4: <b>{ind.get("bias", "—")}</b> · EMA 200 H1: <b>{ind.get("ema", "—")}</b> · zonas vivas: <b>{ind.get("zones", "—")}</b>'
                    + (f' · objetivos: {", ".join(fmt(x) for x in ind.get("targets", []))}' if ind.get("targets") else ""))
        else:
            trig = f'RSI({acc["rsi"]}) = <b>{fmt(ind.get("rsi"), 1)}</b> · compra si &lt; {acc["lo"]} con tendencia alcista · sale si &gt; {acc["hi"]}'
        wins = [x for x in st["trades"] if x["pnl"] > 0]
        gp = sum(x["pnl"] for x in wins); gl = -sum(x["pnl"] for x in st["trades"] if x["pnl"] <= 0)
        pf = f"{gp / gl:.2f}" if gl > 0 else ("∞" if gp > 0 else "—")
        wr = f"{len(wins) / len(st['trades']) * 100:.0f} %" if st["trades"] else "—"
        cards.append(f'''
<div class="card">
  <div class="head"><h2>{acc["name"]}</h2><span class="badge {"open" if st["status"] == "abierto" else "closed"}">{"● Abierto" if st["status"] == "abierto" else "○ Cerrado"}</span></div>
  <div class="sub">{acc["desc"].replace("{SESS_MAD}", sess_mad)}</div>
  <div class="kpis">
    <div><span>Capital</span><b>{fmt(st["equity"])} €</b></div>
    <div><span>Resultado</span><b class="{"up" if pl >= 0 else "dn"}">{pl:+,.2f} € ({pl / CAPITAL_EUR * 100:+.2f} %)</b></div>
    <div><span>Oro</span><b>{fmt(px)} $</b></div>
    <div><span>Caída máx.</span><b>{st["max_dd"] * 100:.2f} %</b></div>
    <div><span>Operaciones</span><b>{len(st["trades"])} · acierto {wr} · PF {pf}</b></div>
    <div><span>Costes + swaps</span><b>{st["costs_paid"] + st["swaps_paid"]:,.2f} €</b></div>
  </div>
  {pos_html}
  <div class="ind">Tendencia {"✔ alcista" if ind.get("trend") == "alcista" else "✘ bajista"} (cierre {fmt(ind.get("close"))} vs EMA {fmt(ind.get("trend_line"))}) · {trig} · ATR {fmt(ind.get("atr"))} → stop a {fmt(ind.get("stop_dist"))} $</div>
  {spark(st["curve"])}
  <details><summary>Eventos</summary><ul>{"".join(f"<li>{html.escape(e)}</li>" for e in st["events"][:25])}</ul></details>
</div>''')
        for x in st["trades"]:
            rows.append((x["exit_time"], f'<tr><td>{acc["name"]}</td><td>{x["side"]}</td><td>{iso_madrid(x["entry_time"])}</td>'
                         f'<td>{iso_madrid(x["exit_time"])}</td><td>{x["qty"]:g}</td><td>{fmt(x["avg_entry"])}</td><td>{fmt(x["exit"])}</td>'
                         f'<td class="{"up" if x["pnl"] >= 0 else "dn"}">{x["pnl"]:+,.2f}</td><td>{x["R"]:+.2f}</td><td>{html.escape(x["reason"])}</td></tr>'))
    rows.sort(reverse=True)
    smc_html = smc_section(s)
    trades_html = "".join(r for _, r in rows) or '<tr><td colspan="10" class="muted">Aún no hay operaciones cerradas</td></tr>'
    note = os.environ.get("PT_NOTE", "Si esta hora tiene más de 2 minutos, el proceso no está corriendo.")
    DASH.write_text(f'''<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="60">
<title>Paper trading · Oro Vantage</title>
<style>
body{{font-family:Segoe UI,system-ui,sans-serif;background:#0f1419;color:#d7dde4;margin:0;padding:24px}}
h1{{margin:0 0 4px;font-size:22px}} h2{{margin:0;font-size:17px}} .muted,.sub{{color:#8b97a5;font-size:13px}}
.top{{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px;margin-bottom:18px}}
.total{{font-size:15px}} .total b{{font-size:22px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(460px,100%),1fr));gap:16px}}
.card{{background:#161c23;border:1px solid #243040;border-radius:10px;padding:16px}}
.head{{display:flex;justify-content:space-between;align-items:center;gap:8px}}
.badge{{font-size:12px;padding:3px 8px;border-radius:12px;white-space:nowrap}} .badge.open{{background:#123d2c;color:#3fb68b}} .badge.closed{{background:#2a2f36;color:#8b97a5}}
.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}}
.kpis div{{background:#1c242d;border-radius:8px;padding:8px}} .kpis span{{display:block;font-size:12px;color:#8b97a5}} .kpis b{{font-size:15px}}
.pos{{border-radius:8px;padding:8px 10px;font-size:14px;margin-bottom:8px}} .pos.long{{background:#123d2c}} .pos.short{{background:#3d1a1a}} .pos.flat{{background:#1c242d;color:#8b97a5}}
.ind{{font-size:13px;color:#aab4bf;margin-bottom:6px}}
.up{{color:#3fb68b}} .dn{{color:#e5534b}} .chart{{width:100%;height:120px}} .base{{stroke:#3a4654;stroke-dasharray:4 4}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}} th,td{{padding:6px 8px;border-bottom:1px solid #243040;text-align:right}} th:first-child,td:first-child{{text-align:left}}
details{{font-size:13px;margin-top:6px}} ul{{padding-left:18px;color:#aab4bf}}
@media (max-width:600px){{body{{padding:12px}} .kpis{{grid-template-columns:repeat(2,1fr)}} table{{display:block;overflow-x:auto}}}}
.tablewrap{{overflow-x:auto;margin-bottom:6px}} table.summary{{margin-top:8px;background:#161c23;border:1px solid #243040;border-radius:10px;border-collapse:separate;border-spacing:0}}
table.summary th{{color:#8b97a5;font-weight:600;font-size:12px}} table.summary td,table.summary th{{white-space:nowrap}} table.summary tr:last-child td{{border-bottom:none}}
.key{{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:8px}}
.tag{{font-size:12px;padding:2px 6px;border-radius:6px}} .tag.long{{background:#123d2c;color:#3fb68b}} .tag.short{{background:#3d1a1a;color:#e5534b}}
.lead{{font-size:11px;color:#8b97a5;margin-left:6px}}
.chartbox{{background:#161c23;border:1px solid #243040;border-radius:10px;padding:12px 14px;margin-top:8px}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:#d7dde4;margin-bottom:6px}} .lg i,.tip i{{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}}
.plot{{position:relative}} #eqc{{width:100%;height:auto;display:block;touch-action:none}}
#eqc .grid{{stroke:#243040;stroke-width:1}} #eqc .base{{stroke:#5b6876;stroke-dasharray:4 4}} #eqc .tick{{stroke:#3a4654}} #eqc .ax{{fill:#8b97a5;font-size:12px}} #eqc .xh{{stroke:#8b97a5;stroke-width:1}}
.tip{{position:absolute;top:8px;background:#0f1419;border:1px solid #3a4654;border-radius:8px;padding:8px 10px;font-size:12px;pointer-events:none;min-width:220px}}
.tip .th{{color:#8b97a5;margin-bottom:4px}} .tip .tr{{display:flex;align-items:center;gap:6px;margin:2px 0}} .tip .tr b{{min-width:80px;text-align:right}} .tip .tr span{{color:#aab4bf}}
.warn{{background:#2d2410;border:1px solid #5c4813;color:#e8c66b;padding:8px 12px;border-radius:8px;font-size:13px;margin-bottom:16px}}
</style></head><body>
<div class="top"><div><h1>Paper trading · Oro (Vantage) · 1000 € por estrategia</h1>
<div class="muted">Dinero ficticio · precio del oro: {price_src} · EURUSD {fx:.4f} · actualizado <b>{madrid(now_utc()):%d/%m/%Y %H:%M}</b> (hora de Madrid) · la página se recarga cada minuto</div></div>
<div class="total">Total: <b>{tot:,.2f} €</b> <span class="{"up" if tot >= tot0 else "dn"}">({(tot / tot0 - 1) * 100:+.2f} %)</span> <span class="muted">de {tot0:,.0f} €</span></div></div>
<div class="warn">Simulación con dinero ficticio. Cada estrategia tiene su propia cuenta de 1000 €. Lotes como en Vantage (mínimo 0,01 lote = 1 oz).
Costes 0,20 $/oz por lado y swap de largos 6 %/año. {html.escape(note)}</div>
<h2>Comparativa</h2>
{summary_table(s, fx)}
<h2 style="margin-top:22px">Capital de cada cuenta</h2>
{equity_chart(s)}
{smc_html}
<h2 style="margin-top:22px">Detalle por cuenta</h2>
<div class="grid">{"".join(cards)}</div>
<h2 style="margin-top:22px">Operaciones cerradas</h2>
<table><tr><th>Cuenta</th><th>Lado</th><th>Entrada (Madrid)</th><th>Salida (Madrid)</th><th>Oz</th><th>Precio entrada</th><th>Precio salida</th><th>P/L €</th><th>R</th><th>Motivo</th></tr>{trades_html}</table>
</body></html>''', encoding="utf-8")

def main():
    s = load_state()
    once = "--once" in sys.argv
    log("Paper trader iniciado" + (" (una pasada)" if once else ""))
    while True:
        s["fx"] = eurusd(s.get("fx"))
        update_offset(s)
        ACC_STATES.clear(); ACC_STATES.update(s["accounts"])   # la cuenta de agentes lee las velas de la gemela
        for acc in ACCOUNTS:
            try:
                process(acc, s["accounts"][acc["id"]], s["fx"])
            except Exception as ex:
                log(f"[{acc['id']}] error: {ex!r}")
                if once: traceback.print_exc()
        s["updated"] = now_utc().isoformat()
        save_state(s); render(s)
        if once: break
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
