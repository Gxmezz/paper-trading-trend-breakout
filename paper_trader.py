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
    dict(id="gold_m5", name="Oro M5 · Pullback", tf="5m", fam="PB", htf="1h", ema=200, rsi=3, lo=5, hi=80,
         mult=2.0, shorts=False, sess=(7, 20), risk=0.02,
         desc="Tendencia EMA 200 de H1 + compra con RSI(3) < 5, venta con RSI(3) > 80 · stop ATR×2 · riesgo 2 % · entradas 07-20 UTC ({SESS_MAD})"),
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
.warn{{background:#2d2410;border:1px solid #5c4813;color:#e8c66b;padding:8px 12px;border-radius:8px;font-size:13px;margin-bottom:16px}}
</style></head><body>
<div class="top"><div><h1>Paper trading · Oro (Vantage) · 1000 € por estrategia</h1>
<div class="muted">Dinero ficticio · precio del oro: {price_src} · EURUSD {fx:.4f} · actualizado <b>{madrid(now_utc()):%d/%m/%Y %H:%M}</b> (hora de Madrid) · la página se recarga cada minuto</div></div>
<div class="total">Total: <b>{tot:,.2f} €</b> <span class="{"up" if tot >= tot0 else "dn"}">({(tot / tot0 - 1) * 100:+.2f} %)</span> <span class="muted">de {tot0:,.0f} €</span></div></div>
<div class="warn">Simulación con dinero ficticio. Cada estrategia tiene su propia cuenta de 1000 €. Lotes como en Vantage (mínimo 0,01 lote = 1 oz).
Costes 0,20 $/oz por lado y swap de largos 6 %/año. {html.escape(note)}</div>
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
