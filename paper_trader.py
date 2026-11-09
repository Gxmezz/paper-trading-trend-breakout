"""
Paper trading (dinero ficticio) de la estrategia Trend Breakout EMA + Donchian + ATR.

- Precios en vivo: oro = perpetuo XAU_USDT de Gate.io (≈ spot), BTC = BTCUSDT de Binance. Sin claves ni órdenes reales.
- Respeta el horario del oro: cerrado vie 21:00 UTC -> dom 22:00 UTC y pausa diaria 21:00-22:00 UTC.
- Costes: oro 0.25 $/oz por lado (Vantage Raw ECN estimado), BTC 0.10 % por lado. Swap por noche.
- Lotes como Vantage: oro en onzas enteras (0.01 lote = 1 oz), BTC en pasos de 0.01.
- Estado en state.json; dashboard en dashboard.html (se refresca solo cada 30 s).

Uso:  python paper_trader.py          (bucle continuo)
      python paper_trader.py --once   (una pasada, para probar)
      python paper_trader.py --reset  (borra el estado y empieza de cero)
"""
import json, math, sys, time, traceback, html
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

import os
HERE = Path(__file__).parent
STATE = Path(os.environ.get("PT_STATE", HERE / "state.json"))
DASH = Path(os.environ.get("PT_DASH", HERE / "dashboard.html"))
LOG = Path(os.environ.get("PT_LOG", HERE / "paper_trader.log"))
# Fuente del oro: gate = perpetuo XAU_USDT de Gate.io (precio spot, accesible desde cualquier país, por defecto)
#                 fapi = XAUUSDT de Binance Futures (bloqueado desde EE. UU.) · spot = PAXGUSDT de Binance
GOLD_API = os.environ.get("PT_GOLD_API", "gate")
GOLD_SYMBOL = {"gate": "XAU_USDT", "fapi": "XAUUSDT", "spot": "PAXGUSDT"}[GOLD_API]
POLL_SECONDS = 20
CAPITAL = 10_000.0
RISK = 0.01          # 1 % del capital por unidad
MAX_UNITS = 3        # piramidación
MAX_LEV = 10.0

ACCOUNTS = [
    dict(id="gold_h1", name="Oro H1", symbol=GOLD_SYMBOL, api=GOLD_API, tf="1h", ema=200, don=40, mult=3.0,
         cost_abs=0.25, cost_pct=0.0, swap_year=0.06, hours="gold", qty_step=1.0, unit="oz"),
    dict(id="gold_m30", name="Oro M30", symbol=GOLD_SYMBOL, api=GOLD_API, tf="30m", ema=100, don=60, mult=3.0,
         cost_abs=0.25, cost_pct=0.0, swap_year=0.06, hours="gold", qty_step=1.0, unit="oz"),
    dict(id="btc_d1", name="BTC Diario", symbol="BTCUSDT", api="spot", tf="1d", ema=200, don=20, mult=3.0,
         cost_abs=0.0, cost_pct=0.001, swap_year=0.20, hours="24/7", qty_step=0.01, unit="BTC"),
]
# data-api.binance.vision = solo datos de mercado, accesible también desde servidores en EE. UU.
BASE = {"fapi": "https://fapi.binance.com/fapi/v1", "spot": "https://data-api.binance.vision/api/v3"}
TF_MS = {"1m": 60_000, "30m": 1_800_000, "1h": 3_600_000, "1d": 86_400_000}

def now_utc(): return datetime.now(timezone.utc)
def ms_to_dt(ms): return datetime.fromtimestamp(ms / 1000, timezone.utc)
def log(msg):
    line = f"{now_utc():%Y-%m-%d %H:%M:%S} UTC  {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f: f.write(line + "\n")

# ---------- horario de mercado ----------
def gold_open(t: datetime) -> bool:
    wd, h = t.weekday(), t.hour            # 0=lunes ... 6=domingo
    if wd == 5: return False                                   # sábado
    if wd == 4 and h >= 21: return False                       # viernes desde 21:00
    if wd == 6 and h < 22: return False                        # domingo hasta 22:00
    if h == 21: return False                                   # pausa diaria 21-22 UTC
    return True

def is_open(acc, t): return True if acc["hours"] == "24/7" else gold_open(t)

# ---------- datos ----------
GATE = "https://api.gateio.ws/api/v4/futures/usdt"
GATE_TF = {"1m": 60, "30m": 1800, "1h": 3600, "1d": 86400}

def gate_klines(acc, interval, limit=1000, start=None):
    sec = GATE_TF[interval]
    p = {"contract": acc["symbol"], "interval": interval}
    if start is None:
        p["limit"] = min(limit, 1999)
    else:
        p["from"] = int(start // 1000)
        p["to"] = int(min(start // 1000 + (min(limit, 1999) - 1) * sec, time.time()))
    r = requests.get(f"{GATE}/candlesticks", params=p, timeout=20)
    r.raise_for_status()
    return [dict(t=int(k["t"]) * 1000, o=float(k["o"]), h=float(k["h"]), l=float(k["l"]), c=float(k["c"]),
                 ct=int(k["t"]) * 1000 + sec * 1000 - 1) for k in r.json()]

def klines(acc, interval, limit=1000, start=None):
    if acc["api"] == "gate":
        return gate_klines(acc, interval, limit, start)
    p = {"symbol": acc["symbol"], "interval": interval, "limit": limit}
    if start is not None: p["startTime"] = int(start)
    r = requests.get(f"{BASE[acc['api']]}/klines", params=p, timeout=20)
    r.raise_for_status()
    return [dict(t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), ct=int(k[6])) for k in r.json()]

def closed_bars(acc, interval, limit=1000, start=None):
    nowms = time.time() * 1000
    bars = [b for b in klines(acc, interval, limit, start) if b["ct"] < nowms]
    if acc["hours"] == "gold":
        bars = [b for b in bars if gold_open(ms_to_dt(b["t"]))]
    return bars

def last_price(acc):
    if acc["api"] == "gate":
        r = requests.get(f"{GATE}/tickers", params={"contract": acc["symbol"]}, timeout=15)
        r.raise_for_status(); return float(r.json()[0]["last"])
    r = requests.get(f"{BASE[acc['api']]}/ticker/price", params={"symbol": acc["symbol"]}, timeout=15)
    r.raise_for_status(); return float(r.json()["price"])

# ---------- indicadores (iguales a TradingView) ----------
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

# ---------- estado ----------
def new_account_state(acc):
    return dict(cash=CAPITAL, units=[], stop=None, last_1m=None, last_sig_bar=None, last_swap_day=None,
                price=None, equity=CAPITAL, peak=CAPITAL, max_dd=0.0, trades=[], curve=[], events=[],
                ind={}, status="iniciando", swaps_paid=0.0, costs_paid=0.0, trade_open=None)

def load_state():
    if STATE.exists() and "--reset" not in sys.argv:
        s = json.loads(STATE.read_text(encoding="utf-8"))
    else:
        s = dict(started=now_utc().isoformat(), accounts={})
    for acc in ACCOUNTS:
        s["accounts"].setdefault(acc["id"], new_account_state(acc))
    return s

def save_state(s):
    tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(s, indent=1), encoding="utf-8"); tmp.replace(STATE)

def cost(acc, px): return acc["cost_abs"] + acc["cost_pct"] * px

def event(st, msg):
    st["events"].insert(0, f"{now_utc():%d/%m %H:%M} UTC · {msg}"); del st["events"][60:]

def equity_at(st, px): return st["cash"] + sum(q * (px - e) for q, e, _ in st["units"])

# ---------- lógica ----------
def close_position(acc, st, px_raw, when, reason):
    c = cost(acc, px_raw); px = px_raw - c
    qty = sum(q for q, _, _ in st["units"])
    pnl = sum(q * (px - e) for q, e, _ in st["units"])
    st["cash"] += pnl; st["costs_paid"] += qty * c
    t = st["trade_open"] or {}
    risk_amt = t.get("risk", 0) or 1
    total = pnl - t.get("swaps", 0)  # swaps ya descontados del cash, se suman para el resultado del trade
    st["trades"].insert(0, dict(entry_time=t.get("time"), exit_time=when, units=len(st["units"]), qty=qty,
                                avg_entry=round(sum(q * e for q, e, _ in st["units"]) / qty, 2),
                                exit=round(px_raw, 2), pnl=round(pnl - t.get("swaps", 0), 2),
                                R=round((pnl - t.get("swaps", 0)) / risk_amt, 2), reason=reason))
    event(st, f"CIERRE {qty:g} {acc['unit']} a {px_raw:,.2f} ({reason}) · P/L {total:+,.2f} $")
    log(f"[{acc['id']}] cierre {qty} @ {px_raw} {reason} pnl={total:.2f}")
    st["units"] = []; st["stop"] = None; st["trade_open"] = None

def open_unit(acc, st, px_raw, dist, when):
    eq = equity_at(st, px_raw)
    expo = sum(q for q, _, _ in st["units"]) * px_raw
    q = min(eq * RISK / dist, max(0.0, eq * MAX_LEV - expo) / px_raw)
    step = acc["qty_step"]; q = math.floor(q / step + 1e-9) * step
    if q < step:
        if step * dist <= eq * RISK * 2:  # lote mínimo sin pasar de 2x el riesgo previsto
            q = step
        else:
            event(st, f"Señal ignorada: el lote mínimo arriesgaría {step * dist:,.0f} $ (> 2 % del capital)"); return
    c = cost(acc, px_raw); fill = px_raw + c
    st["units"].append([q, fill, when]); st["costs_paid"] += q * c
    new_stop = px_raw - dist
    st["stop"] = new_stop if st["stop"] is None else max(st["stop"], new_stop)
    if st["trade_open"] is None: st["trade_open"] = dict(time=when, risk=0.0, swaps=0.0)
    st["trade_open"]["risk"] += q * dist
    event(st, f"COMPRA unidad {len(st['units'])}/{MAX_UNITS}: {q:g} {acc['unit']} a {fill:,.2f} · stop {st['stop']:,.2f}")
    log(f"[{acc['id']}] compra {q} @ {fill} stop={st['stop']}")

def process(acc, st):
    """Repite, minuto a minuto, todo lo ocurrido desde la última ejecución. Determinista:
    da el mismo resultado se ejecute cada 20 s o cada 15 min."""
    t = now_utc(); nowms = time.time() * 1000
    open_now = is_open(acc, t)
    st["status"] = "abierto" if open_now else "cerrado"
    tfms = TF_MS[acc["tf"]]
    bars = closed_bars(acc, acc["tf"], 1000)
    if len(bars) <= max(acc["ema"], acc["don"]) + 5:
        return
    closes = [b["c"] for b in bars]
    E = ema(closes, acc["ema"]); A = atr(bars)
    pos_of = {b["t"]: i for i, b in enumerate(bars)}

    def snapshot(i):
        b = bars[i]; up = max(x["h"] for x in bars[i - acc["don"]:i])
        st["ind"] = dict(bar_time=ms_to_dt(b["t"]).isoformat(), close=b["c"], ema=round(E[i], 2),
                         donchian_high=round(up, 2), atr=round(A[i], 2), stop_dist=round(acc["mult"] * A[i], 2),
                         trend="alcista" if b["c"] > E[i] else "bajista")
        return up

    if st["last_sig_bar"] is None:                    # primer arranque: no se opera la historia
        st["last_sig_bar"] = bars[-1]["t"]
        st["last_1m"] = int(nowms // 60_000 * 60_000) - 60_000
        snapshot(len(bars) - 1)
        event(st, f"Arranque: {acc['name']} listo. Se opera desde la próxima vela cerrada.")

    def on_bar_close(i, fill_px, fill_time):
        """Vela de la estrategia i cerrada: trailing stop y señal; la entrada se ejecuta en fill_px."""
        b = bars[i]; dist = acc["mult"] * A[i]
        up = snapshot(i)
        if st["units"]:
            st["stop"] = max(st["stop"], b["c"] - dist)
        if len(st["units"]) < MAX_UNITS and b["c"] > up and b["c"] > E[i]:
            open_unit(acc, st, fill_px, dist, fill_time)

    start = st["last_1m"] + 60_000
    while start < nowms - 60_000:
        m1 = closed_bars(acc, "1m", 1000, start)
        if not m1:                      # p. ej. fin de semana del oro: no hay velas, saltar
            start += 1000 * 60_000; continue
        for b in m1:
            bt = ms_to_dt(b["t"])
            # 1) velas de la estrategia que han cerrado antes de este minuto -> entrada al open de este minuto
            pend = [x["t"] for x in bars if x["t"] > st["last_sig_bar"] and x["t"] + tfms <= b["t"]]
            for tb in pend:
                on_bar_close(pos_of[tb], b["o"], bt.isoformat())
                st["last_sig_bar"] = tb
            # 2) swap: una noche por rollover (21:00 UTC); el rollover del miércoles cobra 3
            day = (bt - timedelta(hours=22)).date().isoformat() if acc["hours"] == "gold" else bt.date().isoformat()
            if st["units"] and st["last_swap_day"] and day != st["last_swap_day"]:
                nights = 3 if (acc["hours"] == "gold" and datetime.fromisoformat(day).weekday() == 2) else 1
                sw = sum(q for q, _, _ in st["units"]) * b["o"] * acc["swap_year"] / 365 * nights
                st["cash"] -= sw; st["swaps_paid"] += sw
                if st["trade_open"]: st["trade_open"]["swaps"] += sw
                event(st, f"Swap cobrado: {sw:,.2f} $ ({nights} noche{'s' if nights > 1 else ''})")
            st["last_swap_day"] = day
            # 3) stop intravela
            if st["units"] and b["l"] <= st["stop"]:
                close_position(acc, st, min(b["o"], st["stop"]), bt.isoformat(), "stop")
            st["last_1m"] = b["t"]; st["price"] = b["c"]
        start = st["last_1m"] + 60_000
    if open_now:
        try:
            lp = last_price(acc)
            if lp: st["price"] = lp
        except Exception: pass
    # 4) equity
    px = st["price"]
    if px or not st["units"]:
        px = px or 0.0
        st["equity"] = equity_at(st, px)
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
    x0, x1 = xs[0], xs[-1] or 1; lo, hi = min(ys + [CAPITAL]), max(ys + [CAPITAL])
    if hi - lo < 1: hi, lo = hi + 1, lo - 1
    fx = lambda x: 4 + (x - x0) / max(1, x1 - x0) * (w - 8)
    fy = lambda y: h - 6 - (y - lo) / (hi - lo) * (h - 12)
    pts = " ".join(f"{fx(x):.1f},{fy(y):.1f}" for x, y in curve)
    col = "#3fb68b" if ys[-1] >= CAPITAL else "#e5534b"
    return (f'<svg viewBox="0 0 {w} {h}" class="chart"><line x1="0" x2="{w}" y1="{fy(CAPITAL):.1f}" y2="{fy(CAPITAL):.1f}" class="base"/>'
            f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2"/></svg>')

def fmt(v, d=2): return f"{v:,.{d}f}" if isinstance(v, (int, float)) else "—"

def render(s):
    cards = []; rows = []
    tot_eq = sum(st["equity"] for st in s["accounts"].values()); tot_start = CAPITAL * len(ACCOUNTS)
    for acc in ACCOUNTS:
        st = s["accounts"][acc["id"]]; ind = st.get("ind", {}); px = st.get("price")
        pl = st["equity"] - CAPITAL
        qty = sum(q for q, _, _ in st["units"])
        avg = sum(q * e for q, e, _ in st["units"]) / qty if qty else None
        unreal = qty * (px - avg) if qty and px else 0
        trig = ind.get("donchian_high")
        dist_txt = f"{(trig - px):+,.2f} ({(trig / px - 1) * 100:+.2f} %)" if trig and px else "—"
        cond = "✔" if ind.get("trend") == "alcista" else "✘"
        pos_html = (f'<div class="pos long">LARGO · {len(st["units"])}/{MAX_UNITS} unidades · {qty:g} {acc["unit"]} · entrada media {fmt(avg)}'
                    f'<br>Stop (trailing): <b>{fmt(st["stop"])}</b> · P/L abierto: <b class="{"up" if unreal >= 0 else "dn"}">{unreal:+,.2f} $</b></div>'
                    if qty else '<div class="pos flat">Sin posición · esperando ruptura</div>')
        wins = [t for t in st["trades"] if t["pnl"] > 0]
        gp = sum(t["pnl"] for t in wins); gl = -sum(t["pnl"] for t in st["trades"] if t["pnl"] <= 0)
        pf = f"{gp / gl:.2f}" if gl > 0 else ("∞" if gp > 0 else "—")
        cards.append(f'''
<div class="card">
  <div class="head"><h2>{acc["name"]}</h2><span class="badge {"open" if st["status"] == "abierto" else "closed"}">{"● Mercado abierto" if st["status"] == "abierto" else "○ Mercado cerrado"}</span></div>
  <div class="sub">{acc["symbol"]} · EMA {acc["ema"]} · Donchian {acc["don"]} · ATR×{acc["mult"]:g} · hasta {MAX_UNITS} unidades · riesgo {RISK*100:g} %/unidad</div>
  <div class="kpis">
    <div><span>Capital</span><b>{fmt(st["equity"])} $</b></div>
    <div><span>Resultado</span><b class="{"up" if pl >= 0 else "dn"}">{pl:+,.2f} $ ({pl / CAPITAL * 100:+.2f} %)</b></div>
    <div><span>Precio</span><b>{fmt(px)}</b></div>
    <div><span>Caída máx.</span><b>{st["max_dd"] * 100:.2f} %</b></div>
    <div><span>Operaciones</span><b>{len(st["trades"])} · PF {pf}</b></div>
    <div><span>Costes + swaps</span><b>{st["costs_paid"] + st["swaps_paid"]:,.2f} $</b></div>
  </div>
  {pos_html}
  <div class="ind">Tendencia {cond} cierre {fmt(ind.get("close"))} vs EMA {fmt(ind.get("ema"))} · Disparo de compra (máx. Donchian): <b>{fmt(trig)}</b> · distancia {dist_txt} · ATR {fmt(ind.get("atr"))} · stop a {fmt(ind.get("stop_dist"))}</div>
  {spark(st["curve"])}
  <details><summary>Eventos</summary><ul>{"".join(f"<li>{html.escape(e)}</li>" for e in st["events"][:25])}</ul></details>
</div>''')
        for t in st["trades"]:
            rows.append((t["exit_time"], f'<tr><td>{acc["name"]}</td><td>{(t["entry_time"] or "")[:16].replace("T", " ")}</td><td>{t["exit_time"][:16].replace("T", " ")}</td>'
                         f'<td>{t["units"]}</td><td>{t["qty"]:g}</td><td>{fmt(t["avg_entry"])}</td><td>{fmt(t["exit"])}</td>'
                         f'<td class="{"up" if t["pnl"] >= 0 else "dn"}">{t["pnl"]:+,.2f}</td><td>{t["R"]:+.2f}</td><td>{t["reason"]}</td></tr>'))
    rows.sort(reverse=True)
    trades_html = "".join(r for _, r in rows) or '<tr><td colspan="10" class="muted">Aún no hay operaciones cerradas</td></tr>'
    upd = now_utc()
    DASH.write_text(f'''<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><meta http-equiv="refresh" content="60">
<title>Paper trading · Trend Breakout</title>
<style>
body{{font-family:Segoe UI,system-ui,sans-serif;background:#0f1419;color:#d7dde4;margin:0;padding:24px}}
h1{{margin:0 0 4px;font-size:22px}} h2{{margin:0;font-size:18px}} .muted,.sub{{color:#8b97a5;font-size:13px}}
.top{{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:12px;margin-bottom:18px}}
.total{{font-size:15px}} .total b{{font-size:22px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:16px}}
.card{{background:#161c23;border:1px solid #243040;border-radius:10px;padding:16px}}
.head{{display:flex;justify-content:space-between;align-items:center}}
.badge{{font-size:12px;padding:3px 8px;border-radius:12px}} .badge.open{{background:#123d2c;color:#3fb68b}} .badge.closed{{background:#2a2f36;color:#8b97a5}}
.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}}
.kpis div{{background:#1c242d;border-radius:8px;padding:8px}} .kpis span{{display:block;font-size:12px;color:#8b97a5}} .kpis b{{font-size:15px}}
.pos{{border-radius:8px;padding:8px 10px;font-size:14px;margin-bottom:8px}} .pos.long{{background:#123d2c}} .pos.flat{{background:#1c242d;color:#8b97a5}}
.ind{{font-size:13px;color:#aab4bf;margin-bottom:6px}}
.up{{color:#3fb68b}} .dn{{color:#e5534b}} .chart{{width:100%;height:120px}} .base{{stroke:#3a4654;stroke-dasharray:4 4}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}} th,td{{padding:6px 8px;border-bottom:1px solid #243040;text-align:right}} th:first-child,td:first-child{{text-align:left}}
details{{font-size:13px;margin-top:6px}} ul{{padding-left:18px;color:#aab4bf}}
.warn{{background:#2d2410;border:1px solid #5c4813;color:#e8c66b;padding:8px 12px;border-radius:8px;font-size:13px;margin-bottom:16px}}
</style></head><body>
<div class="top"><div><h1>Paper trading · Trend Breakout (EMA + Donchian + ATR)</h1>
<div class="muted">Dinero ficticio · precios en vivo: oro {GOLD_SYMBOL} ({GOLD_API}), BTC Binance · actualizado {upd:%d/%m/%Y %H:%M:%S} UTC · la página se recarga cada minuto</div></div>
<div class="total">Total cartera: <b>{tot_eq:,.2f} $</b> <span class="{"up" if tot_eq >= tot_start else "dn"}">({(tot_eq / tot_start - 1) * 100:+.2f} %)</span></div></div>
<div class="warn">Simulación con dinero ficticio. Costes incluidos: oro 0,25 $/oz por lado, BTC 0,10 % por lado, swaps (oro 6 %/año, BTC 20 %/año). {os.environ.get("PT_NOTE", "Si esta hora tiene más de 2 minutos, el proceso no está corriendo.")}</div>
<div class="grid">{"".join(cards)}</div>
<h2 style="margin-top:22px">Operaciones cerradas</h2>
<table><tr><th>Cuenta</th><th>Entrada</th><th>Salida</th><th>Unid.</th><th>Cantidad</th><th>Entrada media</th><th>Salida</th><th>P/L $</th><th>R</th><th>Motivo</th></tr>{trades_html}</table>
</body></html>''', encoding="utf-8")

def main():
    s = load_state()
    once = "--once" in sys.argv
    log("Paper trader iniciado" + (" (una pasada)" if once else ""))
    while True:
        for acc in ACCOUNTS:
            try:
                process(acc, s["accounts"][acc["id"]])
            except Exception as ex:
                log(f"[{acc['id']}] error: {ex!r}")
                if once: traceback.print_exc()
        s["updated"] = now_utc().isoformat()
        save_state(s); render(s)
        if once: break
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
