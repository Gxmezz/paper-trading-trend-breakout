"""
Cartera BTC (paper trading): 3 estrategias que comparten UNA cuenta de 1000 €.
Selección en strategy/btc_opt.py y strategy/btc_opt2.py (train 2022-24, valid 2025, prueba ene-oct 2026).

  M15 · Pullback            : EMA 200 + RSI(3) < 5 compra / > 95 vende · sale con RSI 60/40 · stop ATR×2 · riesgo 3 %
  M15 · Ruptura volatilidad : apertura del día UTC + 1×rango de ayer · EMA 200 de H1 · stop ATR×3 · solo largos ·
                              riesgo 2 % · entradas 13-21 UTC · cierre antes de medianoche UTC (sin swap)
  M30 · Ruptura cierre diario: EMA 200 de H4 + Donchian 60 + ATR×5 trailing · hasta 3 unidades · solo largos ·
                              riesgo 1 % · cierre antes de medianoche UTC

Condiciones BTCUSD en Vantage (supuestos conservadores; Vantage no publica la ficha):
  1 lote = 1 BTC, mínimo 0,01 BTC · coste 0,02 % por lado · swap 20 %/año en largos y cortos (diario, 00:00 UTC) ·
  apalancamiento máx. 1:10 para toda la cartera · si 0,01 BTC arriesga más del 4 % se descarta la señal.
Precio: BTCUSDT spot de Binance (data-api.binance.vision, los mismos datos que el backtest). BTC cotiza 24/7.

Mismo esquema que el motor del oro: cada pasada repite minuto a minuto lo ocurrido desde la anterior; entradas al
open del minuto siguiente al cierre de la vela, stops con el mínimo/máximo de cada minuto.
"""
import bisect, html, math, time
from datetime import datetime, timezone
import requests

API = "https://data-api.binance.vision/api/v3"
SYMBOL = "BTCUSDT"
CAPITAL_EUR = 1000.0
STEP = 0.01; COST_PCT = 0.0002; SWAP = 0.20; MAX_LEV = 10.0; MAX_RISK = 0.04
TF_MS = {"1m": 60_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}
DAY = 86_400_000

SLEEVES = [
    dict(id="btc_m15_pb", name="BTC M15 · Pullback RSI", tf="15m", fam="PB", htf="same", ema=200, rsi=3, lo=5, hi=60,
         mult=2.0, shorts=True, sess=(7, 20), risk=0.03,
         desc="EMA 200 + RSI(3) &lt; 5 compra / &gt; 95 vende · sale con RSI 60/40 · stop ATR×2 · riesgo 3 % · entradas {S}"),
    dict(id="btc_m15_vb", name="BTC M15 · Ruptura de volatilidad", tf="15m", fam="VB", htf="1h", ema=200, k=1.0, mult=3.0,
         shorts=False, sess=(13, 21), risk=0.02, flat=True,
         desc="Compra si cierra sobre apertura del día + 1×rango de ayer · EMA 200 de H1 · stop ATR×3 · riesgo 2 % · entradas {S} · cierra antes de las {M} (sin swap)"),
    dict(id="btc_m30_boh", name="BTC M30 · Ruptura con cierre diario", tf="30m", fam="BOH", htf="4h", ema=200, don=60, mult=5.0,
         units=3, shorts=False, sess=(0, 24), risk=0.01, flat=True,
         desc="EMA 200 de H4 + ruptura Donchian 60 + ATR×5 trailing · hasta 3 unidades · solo largos · riesgo 1 % · cierra antes de las {M}"),
]

def ms_to_dt(ms): return datetime.fromtimestamp(ms / 1000, timezone.utc)

# ---------- datos ----------
_CACHE = {}
def klines(interval, limit=1000, start=None):
    p = {"symbol": SYMBOL, "interval": interval, "limit": limit}
    if start is not None: p["startTime"] = int(start)
    r = requests.get(f"{API}/klines", params=p, timeout=20); r.raise_for_status()
    nowms = time.time() * 1000
    return [dict(t=int(k[0]), o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4])) for k in r.json() if int(k[6]) < nowms]

def bars(interval):
    if interval not in _CACHE: _CACHE[interval] = klines(interval)
    return _CACHE[interval]

def last_price():
    r = requests.get(f"{API}/ticker/price", params={"symbol": SYMBOL}, timeout=15); r.raise_for_status()
    return float(r.json()["price"])

# ---------- indicadores (iguales a los del backtest) ----------
def ema(vals, n):
    a = 2 / (n + 1); out = []; e = vals[0]
    for v in vals: e = a * v + (1 - a) * e; out.append(e)
    return out

def atr(bs, n=14):
    out = []; a = None; pc = None
    for b in bs:
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

def indicators(sl, bs):
    closes = [b["c"] for b in bs]; A = atr(bs)
    if sl["htf"] == "same": T = ema(closes, sl["ema"])
    else:   # EMA de la temporalidad superior, solo con velas ya cerradas
        hb = bars(sl["htf"]); hE = ema([b["c"] for b in hb], sl["ema"]); hms = TF_MS[sl["htf"]]
        hclose = [b["t"] + hms for b in hb]
        T = [hE[j] if (j := bisect.bisect_right(hclose, b["t"]) - 1) >= 0 else float("nan") for b in bs]
    X = None
    if sl["fam"] == "PB": X = rsi(closes, sl["rsi"])
    elif sl["fam"] == "VB":   # (apertura del día UTC, rango del día anterior)
        days = {}
        for b in bs:
            x = days.setdefault(b["t"] // DAY, dict(o=b["o"], h=b["h"], l=b["l"]))
            x["h"] = max(x["h"], b["h"]); x["l"] = min(x["l"], b["l"])
        X = [(days[b["t"] // DAY]["o"], p["h"] - p["l"]) if (p := days.get(b["t"] // DAY - 1)) else (None, None) for b in bs]
    return T, A, X

# ---------- estado ----------
def new_sleeve():
    return dict(pnl=0.0, pos=0, units=[], stop=None, last_1m=None, last_sig_bar=None, last_day=None, price=None,
                trades=[], events=[], ind={}, swaps_paid=0.0, costs_paid=0.0, trade_open=None, skipped=0)

def ensure(s):
    B = s.setdefault("btc", {})
    B.setdefault("started", datetime.now(timezone.utc).isoformat())
    for k, v in dict(equity=CAPITAL_EUR, peak=CAPITAL_EUR, max_dd=0.0, curve=[], price=None, status="iniciando").items():
        B.setdefault(k, v)
    sl = B.setdefault("sleeves", {})
    for x in SLEEVES: sl.setdefault(x["id"], new_sleeve())
    return B

def unreal_usd(st, px): return sum(st["pos"] * q * (px - e) for q, e, _ in st["units"]) if px else 0.0

class Book:
    """Cuenta común: capital = 1000 € + P/L realizado y abierto de las tres estrategias."""
    def __init__(self, B, fx, madrid): self.B, self.fx, self.madrid = B, fx, madrid
    def st(self, sid): return self.B["sleeves"][sid]
    def equity(self, px):
        return CAPITAL_EUR + sum(st["pnl"] + unreal_usd(st, px) / self.fx for st in self.B["sleeves"].values())
    def exposure(self, px): return sum(sum(q for q, _, _ in st["units"]) for st in self.B["sleeves"].values()) * px
    def event(self, st, msg):
        st["events"].insert(0, f"{self.madrid(datetime.now(timezone.utc)):%d/%m %H:%M} · {msg}"); del st["events"][60:]

    def close(self, sl, st, px_raw, when, reason):
        pos = st["pos"]; c = COST_PCT * px_raw; px = px_raw - pos * c
        qty = sum(q for q, _, _ in st["units"])
        pnl_usd = sum(pos * q * (px - e) for q, e, _ in st["units"])
        st["pnl"] += pnl_usd / self.fx; st["costs_paid"] += qty * c / self.fx
        t = st["trade_open"] or {}
        tot = pnl_usd / self.fx - t.get("swaps", 0)
        st["trades"].insert(0, dict(side="LARGO" if pos == 1 else "CORTO", entry_time=t.get("time"), exit_time=when,
                                    units=len(st["units"]), qty=round(qty, 2),
                                    avg_entry=round(sum(q * e for q, e, _ in st["units"]) / qty, 2), exit=round(px_raw, 2),
                                    pnl=round(tot, 2), R=round(tot / (t.get("risk") or 1), 2), reason=reason))
        self.event(st, f"CIERRE {'largo' if pos == 1 else 'corto'} {qty:g} BTC a {px_raw:,.2f} ({reason}) · {tot:+,.2f} €")
        st["units"] = []; st["pos"] = 0; st["stop"] = None; st["trade_open"] = None

    def open(self, sl, st, side, px_raw, dist, when):
        eq_usd = self.equity(px_raw) * self.fx
        q = round(math.floor(eq_usd * sl["risk"] / dist / STEP + 1e-9) * STEP, 2)
        if q < STEP:
            if STEP * dist <= eq_usd * MAX_RISK: q = STEP
            else:
                st["skipped"] += 1
                self.event(st, f"Señal descartada: 0,01 BTC arriesgaría {STEP * dist / self.fx:,.0f} € (> {MAX_RISK:.0%} de la cuenta)"); return
        if self.exposure(px_raw) + q * px_raw > eq_usd * MAX_LEV:
            st["skipped"] += 1
            self.event(st, "Señal descartada: la cartera superaría el apalancamiento máximo (1:10)"); return
        fill = px_raw * (1 + side * COST_PCT)
        st["units"].append([q, fill, when]); st["costs_paid"] += q * COST_PCT * px_raw / self.fx
        ns = px_raw - side * dist
        if st["pos"] == 0: st["pos"] = side; st["stop"] = ns
        else: st["stop"] = max(st["stop"], ns) if side == 1 else min(st["stop"], ns)
        if st["trade_open"] is None: st["trade_open"] = dict(time=when, risk=0.0, swaps=0.0)
        st["trade_open"]["risk"] += q * dist / self.fx
        extra = f" unidad {len(st['units'])}/{sl.get('units', 1)}" if sl.get("units", 1) > 1 else ""
        self.event(st, f"{'COMPRA' if side == 1 else 'VENTA'}{extra}: {q:g} BTC a {fill:,.2f} · stop {st['stop']:,.2f} · riesgo {q * dist / self.fx:,.0f} €")

def on_bar_close(book, sl, st, bs, i, T, A, X, fill_px, when):
    b = bs[i]; dist = sl["mult"] * A[i]; tfms = TF_MS[sl["tf"]]
    ind = dict(bar_time=ms_to_dt(b["t"]).isoformat(), close=b["c"], trend_line=round(T[i], 2), atr=round(A[i], 2),
               stop_dist=round(dist, 2), trend="alcista" if b["c"] > T[i] else "bajista")
    if sl["fam"] == "BOH":
        ind["don_hi"] = round(max(x["h"] for x in bs[i - sl["don"]:i]), 2); ind["don_lo"] = round(min(x["l"] for x in bs[i - sl["don"]:i]), 2)
    elif sl["fam"] == "VB":
        dop, rng = X[i]; ind["vb_hi"] = round(dop + sl["k"] * rng, 2) if dop is not None else None
    else: ind["rsi"] = round(X[i], 1)
    st["ind"] = ind
    if dist <= 0 or T[i] != T[i]: return
    in_sess = sl["sess"][0] <= ms_to_dt(b["t"]).hour < sl["sess"][1]
    last_of_day = sl.get("flat") and (b["t"] + tfms) % DAY == 0      # última vela del día UTC: cierra y no abre
    if sl["fam"] == "BOH":
        if st["pos"]:
            ns = b["c"] - st["pos"] * dist
            st["stop"] = max(st["stop"], ns) if st["pos"] == 1 else min(st["stop"], ns)
        if last_of_day:
            if st["pos"]: book.close(sl, st, fill_px, when, "cierre diario (sin swap)")
            return
        if not in_sess or len(st["units"]) >= sl["units"]: return
        if b["c"] > ind["don_hi"] and b["c"] > T[i] and st["pos"] >= 0: book.open(sl, st, 1, fill_px, dist, when)
        elif sl["shorts"] and b["c"] < ind["don_lo"] and b["c"] < T[i] and st["pos"] <= 0: book.open(sl, st, -1, fill_px, dist, when)
    elif sl["fam"] == "VB":
        if last_of_day:
            if st["pos"]: book.close(sl, st, fill_px, when, "cierre diario (sin swap)")
            return
        if st["pos"] or not in_sess or ind.get("vb_hi") is None: return
        if b["c"] > ind["vb_hi"] and b["c"] > T[i]: book.open(sl, st, 1, fill_px, dist, when)
    else:  # PB
        r = X[i]
        if st["pos"] == 1 and r > sl["hi"]: book.close(sl, st, fill_px, when, f"RSI {r:.0f} > {sl['hi']}"); return
        if st["pos"] == -1 and r < 100 - sl["hi"]: book.close(sl, st, fill_px, when, f"RSI {r:.0f} < {100 - sl['hi']}"); return
        if st["pos"] == 0 and in_sess:
            if b["c"] > T[i] and r < sl["lo"]: book.open(sl, st, 1, fill_px, dist, when)
            elif sl["shorts"] and b["c"] < T[i] and r > 100 - sl["lo"]: book.open(sl, st, -1, fill_px, dist, when)

def step(s, fx, madrid, log=print):
    """Una pasada: repite minuto a minuto las tres estrategias en orden cronológico sobre la cuenta común."""
    _CACHE.clear()
    B = ensure(s); book = Book(B, fx, madrid); nowms = time.time() * 1000
    ctx = {}
    for sl in SLEEVES:
        bs = bars(sl["tf"])
        if len(bs) < 300: return log("BTC: pocas velas, se espera")
        st = book.st(sl["id"])
        if st["last_sig_bar"] is None:
            st["last_sig_bar"] = bs[-1]["t"]
            book.event(st, "Arranque: estrategia lista. Se opera desde la próxima vela cerrada.")
        ctx[sl["id"]] = (bs, *indicators(sl, bs), {b["t"]: i for i, b in enumerate(bs)})
    if B.get("last_1m") is None: B["last_1m"] = int(nowms // 60_000 * 60_000) - 60_000
    start = B["last_1m"] + 60_000
    while start < nowms - 60_000:
        m1 = klines("1m", 1000, start)
        if not m1: start += 1000 * 60_000; continue
        for m in m1:
            when = ms_to_dt(m["t"]).isoformat(); day = m["t"] // DAY
            for sl in SLEEVES:
                st = book.st(sl["id"]); bs, T, A, X, pos_of = ctx[sl["id"]]; tfms = TF_MS[sl["tf"]]
                for tb in [x["t"] for x in bs if x["t"] > st["last_sig_bar"] and x["t"] + tfms <= m["t"]]:
                    on_bar_close(book, sl, st, bs, pos_of[tb], T, A, X, m["o"], when)
                    st["last_sig_bar"] = tb
                if st["units"] and st["last_day"] is not None and day != st["last_day"]:   # swap diario 00:00 UTC
                    sw = sum(q for q, _, _ in st["units"]) * m["o"] * SWAP / 365 / fx
                    st["pnl"] -= sw; st["swaps_paid"] += sw
                    if st["trade_open"]: st["trade_open"]["swaps"] += sw
                    book.event(st, f"Swap cobrado: {sw:,.2f} €")
                st["last_day"] = day
                if st["units"]:
                    hit = m["l"] <= st["stop"] if st["pos"] == 1 else m["h"] >= st["stop"]
                    if hit:
                        px = min(m["o"], st["stop"]) if st["pos"] == 1 else max(m["o"], st["stop"])
                        book.close(sl, st, px, when, "stop")
                st["price"] = m["c"]
            B["last_1m"] = m["t"]; B["price"] = m["c"]
            eq = book.equity(m["c"])                 # caída máxima minuto a minuto
            B["peak"] = max(B["peak"], eq); B["max_dd"] = min(B["max_dd"], eq / B["peak"] - 1)
        start = B["last_1m"] + 60_000
    try: B["price"] = last_price()
    except Exception: pass
    for st in B["sleeves"].values(): st["price"] = B["price"]
    B["status"] = "abierto"
    B["equity"] = book.equity(B["price"])
    B["peak"] = max(B["peak"], B["equity"]); B["max_dd"] = min(B["max_dd"], B["equity"] / B["peak"] - 1)
    if not B["curve"] or nowms - B["curve"][-1][0] >= 5 * 60_000:
        B["curve"].append([int(nowms), round(B["equity"], 2)])
        if len(B["curve"]) > 6000: B["curve"] = B["curve"][::2]

# ---------- dashboard ----------
def _fmt(v, d=2): return f"{v:,.{d}f}" if isinstance(v, (int, float)) else "—"
def _stats(trades):
    wins = [x for x in trades if x["pnl"] > 0]
    gp = sum(x["pnl"] for x in wins); gl = -sum(x["pnl"] for x in trades if x["pnl"] <= 0)
    pf = f"{gp / gl:.2f}" if gl > 0 else ("∞" if gp > 0 else "—")
    return len(trades), (f"{len(wins) / len(trades) * 100:.0f} %" if trades else "—"), pf

def section(s, fx, madrid, iso_madrid, spark, note=""):
    B = ensure(s); px = B.get("price"); pl = B["equity"] - CAPITAL_EUR
    off = int(madrid(datetime.now(timezone.utc)).utcoffset().total_seconds() // 3600)
    hm = lambda h: f"{(h + off) % 24 or (24 if h else 0):02d}"
    all_tr = [x for st in B["sleeves"].values() for x in st["trades"]]
    n, wr, pf = _stats(all_tr)
    costs = sum(st["costs_paid"] + st["swaps_paid"] for st in B["sleeves"].values())
    expo = sum(sum(q for q, _, _ in st["units"]) for st in B["sleeves"].values()) * (px or 0)
    cards, rows, srows = [], [], []
    for sl in SLEEVES:
        st = B["sleeves"][sl["id"]]; ind = st.get("ind", {})
        qty = sum(q for q, _, _ in st["units"]); un = unreal_usd(st, px) / fx if qty else 0.0
        spl = st["pnl"] + un; sn, swr, spf = _stats(st["trades"])
        if qty:
            avg = sum(q * e for q, e, _ in st["units"]) / qty
            pos_html = (f'<div class="pos {"long" if st["pos"] == 1 else "short"}">{"LARGO" if st["pos"] == 1 else "CORTO"} · {len(st["units"])} unidad(es) · '
                        f'{qty:g} BTC (= {qty:.2f} lotes) · entrada media {_fmt(avg)}<br>Stop: <b>{_fmt(st["stop"])}</b> · '
                        f'P/L abierto: <b class="{"up" if un >= 0 else "dn"}">{un:+,.2f} €</b></div>')
            ptag = f'<span class="tag {"long" if st["pos"] == 1 else "short"}">{"LARGO" if st["pos"] == 1 else "CORTO"} {qty:g} BTC</span>'
        else:
            pos_html = '<div class="pos flat">Sin posición · esperando señal</div>'; ptag = '<span class="muted">—</span>'
        if sl["fam"] == "BOH": trig = f'Compra si cierra &gt; <b>{_fmt(ind.get("don_hi"))}</b>'
        elif sl["fam"] == "VB": trig = f'Compra si cierra &gt; <b>{_fmt(ind.get("vb_hi"))}</b>'
        else: trig = f'RSI({sl["rsi"]}) = <b>{_fmt(ind.get("rsi"), 1)}</b> · compra &lt; {sl["lo"]} / vende &gt; {100 - sl["lo"]}'
        S = sl["sess"]; sess = "24 h" if S == (0, 24) else f"{hm(S[0])}-{hm(S[1])} h en Madrid"
        desc = sl["desc"].replace("{S}", sess).replace("{M}", f"{hm(0)}:00 de Madrid")
        srows.append(f'<tr><td>{sl["name"]}</td><td class="{"up" if spl > 0 else "dn" if spl < 0 else ""}">{spl:+,.2f} €</td>'
                     f'<td>{sn}</td><td>{swr}</td><td>{spf}</td><td>{ptag}</td></tr>')
        cards.append(f'''
<div class="card">
  <div class="head"><h2>{sl["name"]}</h2><span class="badge open">● 24/7</span></div>
  <div class="sub">{desc}</div>
  <div class="kpis">
    <div><span>P/L de la estrategia</span><b class="{"up" if spl >= 0 else "dn"}">{spl:+,.2f} €</b></div>
    <div><span>Operaciones</span><b>{sn} · acierto {swr} · PF {spf}</b></div>
    <div><span>Costes + swaps</span><b>{st["costs_paid"] + st["swaps_paid"]:,.2f} €</b></div>
  </div>
  {pos_html}
  <div class="ind">Tendencia {"✔ alcista" if ind.get("trend") == "alcista" else "✘ bajista"} (cierre {_fmt(ind.get("close"))} vs EMA {_fmt(ind.get("trend_line"))}) · {trig} · ATR {_fmt(ind.get("atr"))} → stop a {_fmt(ind.get("stop_dist"))} $</div>
  <details><summary>Eventos</summary><ul>{"".join(f"<li>{html.escape(e)}</li>" for e in st["events"][:25])}</ul></details>
</div>''')
        for x in st["trades"]:
            rows.append((x["exit_time"], f'<tr><td>{sl["name"]}</td><td>{x["side"]}</td><td>{iso_madrid(x["entry_time"])}</td>'
                         f'<td>{iso_madrid(x["exit_time"])}</td><td>{x["qty"]:g}</td><td>{_fmt(x["avg_entry"])}</td><td>{_fmt(x["exit"])}</td>'
                         f'<td class="{"up" if x["pnl"] >= 0 else "dn"}">{x["pnl"]:+,.2f}</td><td>{x["R"]:+.2f}</td><td>{html.escape(x["reason"])}</td></tr>'))
    rows.sort(reverse=True)
    trades = "".join(r for _, r in rows) or '<tr><td colspan="10" class="muted">Aún no hay operaciones cerradas</td></tr>'
    return f'''<section class="view" id="v-btc">
<div class="top"><div><h1>Paper trading · BTC (Vantage, BTCUSD) · 3 estrategias en una cuenta de 1000 €</h1>
<div class="muted">Dinero ficticio · precio: BTCUSDT spot de Binance · <b>{_fmt(px)} $</b> · EURUSD {fx:.4f} · actualizado <b>{madrid(datetime.now(timezone.utc)):%d/%m/%Y %H:%M}</b> (hora de Madrid)</div></div>
<div class="total">Capital: <b>{B["equity"]:,.2f} €</b> <span class="{"up" if pl >= 0 else "dn"}">({pl / CAPITAL_EUR * 100:+.2f} %)</span> <span class="muted">de {CAPITAL_EUR:,.0f} €</span></div></div>
<div class="warn">Simulación con dinero ficticio. Las 3 estrategias comparten la cuenta: cada una calcula su tamaño sobre el capital total.
Condiciones de Vantage estimadas: mínimo 0,01 BTC, coste 0,02 % por lado, swap 20 %/año en largos y cortos (00:00 UTC), apalancamiento máx. 1:10 para toda la cartera. {html.escape(note)}</div>
<div class="card">
  <div class="kpis">
    <div><span>Capital</span><b>{B["equity"]:,.2f} €</b></div>
    <div><span>Resultado</span><b class="{"up" if pl >= 0 else "dn"}">{pl:+,.2f} € ({pl / CAPITAL_EUR * 100:+.2f} %)</b></div>
    <div><span>Caída máx.</span><b>{B["max_dd"] * 100:.2f} %</b></div>
    <div><span>Operaciones</span><b>{n} · acierto {wr} · PF {pf}</b></div>
    <div><span>Costes + swaps</span><b>{costs:,.2f} €</b></div>
    <div><span>Exposición</span><b>{expo / fx:,.0f} € (x{expo / max(1.0, B["equity"] * fx):.1f})</b></div>
  </div>
  {spark(B["curve"])}
</div>
<h2 style="margin-top:22px">Estrategias</h2>
<div class="tablewrap"><table class="summary"><tr><th>Estrategia</th><th>P/L</th><th>Operaciones</th><th>Acierto</th><th>PF</th><th>Posición abierta</th></tr>{"".join(srows)}</table></div>
<h2 style="margin-top:22px">Detalle por estrategia</h2>
<div class="grid">{"".join(cards)}</div>
<h2 style="margin-top:22px">Operaciones cerradas</h2>
<table><tr><th>Estrategia</th><th>Lado</th><th>Entrada (Madrid)</th><th>Salida (Madrid)</th><th>BTC</th><th>Precio entrada</th><th>Precio salida</th><th>P/L €</th><th>R</th><th>Motivo</th></tr>{trades}</table>
</section>'''
