"""Paper trading de copia de carteras públicas de Hyperliquid (dinero ficticio).

Dos cuentas de 1000 €, cada una con 10 "subcuentas" (una por cartera copiada):
  - copy_s2  : las 10 carteras elegidas con S2 (aprobó el examen de strategy/lab/copy_hl/).
  - copy_rnd : 10 carteras copiables al azar (control).
Las carteras salen de strategy/lab/copy_hl/seleccion.json (se renueva el día 1 de cada mes).

Cada minuto (dentro del bucle de paper_trader.py):
  - Lee las posiciones y el valor de cuenta de cada cartera (clearinghouseState).
  - Posición objetivo de la subcuenta = posición de la cartera × (base / valor de su cuenta),
    con apalancamiento máximo 3× sobre la base. Solo las 9 monedas del examen.
  - Ajusta al precio medio actual con comisión 0,045 % + deslizamiento 0,03 % por lado.
  - Funding horario con la tasa actual de cada moneda.
  - Cuenta las órdenes por debajo del mínimo real de Hyperliquid (10 $).
Mismas reglas que el examen; el retraso real es el del sondeo (≤ ~1 min).
"""
import html, json, time
from datetime import datetime, timezone
from pathlib import Path

import requests

HERE = Path(__file__).parent
SEL = HERE / "strategy" / "lab" / "copy_hl" / "seleccion.json"
INFO = "https://api.hyperliquid.xyz/info"
CAPITAL = 1000.0           # € por cuenta
K = 10
COST = 0.00045 + 0.0003    # por lado
MAXLEV = 3.0
MIN_ORDER_USD = 10.0
EVAL_START = "2026-11-17"  # evaluación fijada de antemano (ver PREREGISTRO_VIVO.md)
EVAL_END = "2027-02-16"
ACCS = [("copy_s2", "Copia S2 · 10 mejores carteras (rentab./caída 180 d)", "copia"),
        ("copy_rnd", "Control · 10 carteras copiables al azar", "azar")]

S = requests.Session()


def now():
    return datetime.now(timezone.utc)


def post(p, timeout=15):
    r = S.post(INFO, json=p, timeout=timeout)
    r.raise_for_status()
    return r.json()


def market():
    meta, ctx = post({"type": "metaAndAssetCtxs"})
    out = {}
    for a, c in zip(meta["universe"], ctx):
        mid = c.get("midPx") or c.get("markPx")
        if mid:
            out[a["name"]] = (float(mid), float(c.get("funding") or 0))
    return out


def wallet(addr):
    d = post({"type": "clearinghouseState", "user": addr})
    pos = {p["position"]["coin"]: float(p["position"]["szi"]) for p in d.get("assetPositions", [])}
    return pos, float(d["marginSummary"]["accountValue"])


def new_sleeve(addr, base):
    return dict(addr=addr, cash=base, q={}, base=base, since=now().isoformat(timespec="seconds"),
                orders=0, below_min=0, costs=0.0, funding=0.0, err=0, wav=None, wav_t=0, wpos=None)


def sleeve_eq(sl, px, fx):
    return sl["cash"] + sum(q * px[c][0] / fx for c, q in sl["q"].items() if c in px)


def trade(acc, sl, c, dq, p, fx, why):
    usd = abs(dq * p)
    cost = usd * COST / fx
    sl["cash"] -= dq * p / fx + cost
    sl["q"][c] = sl["q"].get(c, 0.0) + dq
    if abs(sl["q"][c]) < 1e-12:
        sl["q"].pop(c)
    sl["orders"] += 1; sl["costs"] += cost
    small = usd < MIN_ORDER_USD
    sl["below_min"] += small
    acc["orders"].insert(0, dict(t=now().isoformat(timespec="seconds"), w=sl["addr"], coin=c,
                                 side="compra" if dq > 0 else "venta", usd=round(usd, 2), px=p, small=small, why=why))
    del acc["orders"][300:]


def new_acc():
    return dict(month=None, sleeves=[], cash=CAPITAL, orders=[], curve=[], peak=CAPITAL, max_dd=0.0,
                equity=CAPITAL, events=[], last_fund_h=None, start=now().isoformat(timespec="seconds"))


def event(acc, msg):
    acc["events"].insert(0, f"{now():%d/%m %H:%M} UTC · {msg}"); del acc["events"][60:]


def rebalance(acc, addrs, month, px, fx):
    E = acc["cash"] + sum(sleeve_eq(sl, px, fx) for sl in acc["sleeves"])
    keep = {sl["addr"]: sl for sl in acc["sleeves"]}
    for sl in acc["sleeves"]:
        if sl["addr"] not in addrs:
            for c, q in list(sl["q"].items()):
                if c in px:
                    trade(acc, sl, c, -q, px[c][0], fx, "sale de la selección")
    E = acc["cash"] + sum(sleeve_eq(sl, px, fx) for sl in acc["sleeves"])
    new = []
    for a in addrs[:K]:
        if a in keep:
            sl = keep[a]
            sl["cash"] += E / K - sleeve_eq(sl, px, fx); sl["base"] = E / K
        else:
            sl = new_sleeve(a, E / K)
        new.append(sl)
    acc["sleeves"], acc["cash"], acc["month"] = new, E - E / K * len(new), month
    event(acc, f"Selección {month}: {len(new)} carteras, capital {E:,.2f} €")


def follow(acc, sl, px, fx, coins):
    """Como en el examen: solo se opera una moneda cuando la cartera cambia su posición en ella
    (o al entrar la subcuenta, para copiar lo que ya tiene abierto). El valor de su cuenta se
    actualiza una vez por semana, así que su P/L flotante no genera microajustes."""
    try:
        pos, av = wallet(sl["addr"])
        sl["err"] = 0
    except Exception:
        sl["err"] += 1
        return
    t = time.time()
    if sl.get("wav") is None or t - sl.get("wav_t", 0) >= 7 * 86400:
        sl["wav"], sl["wav_t"] = av, t
    first = sl.get("wpos") is None
    prev = sl.get("wpos") or {}
    sl["wpos"] = {c: v for c, v in pos.items() if c in coins}
    # Igual que el examen: si el valor de la cuenta de perpetuos es ≤ 1 $ (saldo en la cuenta
    # unificada/spot), se usa 1 $, así que el tamaño queda en el tope de 3× la base.
    ratio = sl["base"] * fx / (sl["wav"] if sl["wav"] > 1 else 1.0)
    base_usd = sl["base"] * fx
    changed = [c for c in coins if c in px and (first or abs(pos.get(c, 0.0) - prev.get(c, 0.0)) > 1e-12)]
    for c in changed:
        p = px[c][0]; cur = sl["q"].get(c, 0.0)
        tgt = pos.get(c, 0.0) * ratio
        other = sum(abs(q * px[k][0]) for k, q in sl["q"].items() if k != c and k in px)
        room = max(MAXLEV * base_usd - other, 0.0)
        if abs(tgt * p) > room:
            keepq = abs(cur) if (cur > 0) == (tgt > 0) else 0.0
            tgt = (1 if tgt > 0 else -1) * min(abs(tgt), max(room / p, keepq))
        dq = tgt - cur
        if abs(dq * p) < 1e-6:
            continue
        trade(acc, sl, c, dq, p, fx, "entra con lo que ya tenía" if first else "sigue a la cartera")


def step(s, fx, log=print):
    st = s.setdefault("copy_hl", {})
    if not SEL.exists():
        return
    sel = json.loads(SEL.read_text())
    coins = set(sel.get("coins", []))
    try:
        mk = market()
    except Exception as ex:
        log(f"copy_hl: sin precios ({ex!r})"); return
    px = {c: mk[c] for c in coins if c in mk}
    st["px"] = {c: v[0] for c, v in px.items()}
    st["btc"] = mk.get("BTC", (None,))[0]
    st.setdefault("btc_start", st["btc"])
    st["sel"] = {k: sel.get(k) for k in ("month", "created", "datos_hasta", "n_copiables")}
    h = int(time.time() // 3600)
    for aid, _, key in ACCS:
        acc = st.setdefault(aid, new_acc())
        if acc["month"] != sel["month"]:
            rebalance(acc, [x["addr"] for x in sel[key]], sel["month"], px, fx)
        if acc["last_fund_h"] is not None and h > acc["last_fund_h"]:
            for sl in acc["sleeves"]:
                for c, q in sl["q"].items():
                    if c in px:
                        f = -q * px[c][0] * px[c][1] * (h - acc["last_fund_h"]) / fx
                        sl["cash"] += f; sl["funding"] += f
        acc["last_fund_h"] = h
        for sl in acc["sleeves"]:
            follow(acc, sl, px, fx, coins)
        eq = acc["cash"] + sum(sleeve_eq(sl, px, fx) for sl in acc["sleeves"])
        acc["equity"] = eq
        acc["peak"] = max(acc["peak"], eq)
        acc["max_dd"] = max(acc["max_dd"], 1 - eq / acc["peak"])
        t = int(time.time())
        if not acc["curve"] or t - acc["curve"][-1][0] >= 3600:
            acc["curve"].append([t, round(eq, 2)])


# ------------------------------------------------------------------ dashboard
def _f(v, d=2):
    return f"{v:,.{d}f}" if isinstance(v, (int, float)) else "—"


def section(s, fx, spark, fmt_time):
    st = s.get("copy_hl") or {}
    if not st:
        return '<p class="muted">El copy trading aún no ha arrancado.</p>'
    px = st.get("px", {})
    sel = st.get("sel") or {}
    btc_r = (st["btc"] / st["btc_start"] - 1) if st.get("btc") and st.get("btc_start") else None
    rows, cards = [], []
    for aid, name, _ in ACCS:
        acc = st.get(aid)
        if not acc:
            continue
        pl = acc["equity"] - CAPITAL
        n = sum(sl["orders"] for sl in acc["sleeves"]); small = sum(sl["below_min"] for sl in acc["sleeves"])
        days = max((now() - datetime.fromisoformat(acc["start"])).total_seconds() / 86400, 1 / 24)
        rows.append(f'<tr><td>{name}</td><td>{_f(acc["equity"])} €</td><td class="{"up" if pl >= 0 else "dn"}">{pl:+,.2f} € '
                    f'({pl / CAPITAL * 100:+.2f} %)</td><td>{acc["max_dd"] * 100:.2f} %</td><td>{n}</td><td>{n / days:.1f}</td>'
                    f'<td>{small / n * 100 if n else 0:.0f} %</td></tr>')
        srows = []
        for sl in acc["sleeves"]:
            e = sl["cash"] + sum(q * px.get(c, 0) / fx for c, q in sl["q"].items())
            r = e / sl["base"] - 1 if sl["base"] else 0
            posi = ", ".join(f'{"L" if q > 0 else "S"} {c} {abs(q * px.get(c, 0)):,.0f} $' for c, q in sl["q"].items()) or "sin posición"
            srows.append(f'<tr><td><a href="https://hypurrscan.io/address/{sl["addr"]}" target="_blank" rel="noopener" style="color:#7fb2ff">'
                         f'{sl["addr"][:8]}…{sl["addr"][-4:]}</a></td><td>{_f(e)} €</td><td class="{"up" if r >= 0 else "dn"}">{r * 100:+.2f} %</td>'
                         f'<td>{html.escape(posi)}</td><td>{sl["orders"]}</td><td>{"⚠ sin datos" if sl["err"] >= 5 else ""}</td></tr>')
        cards.append(f'''<div class="card"><div class="head"><h2>{name}</h2></div>
<div class="kpis"><div><span>Capital</span><b>{_f(acc["equity"])} €</b></div>
<div><span>Resultado</span><b class="{"up" if pl >= 0 else "dn"}">{pl / CAPITAL * 100:+.2f} %</b></div>
<div><span>Caída máx.</span><b>{acc["max_dd"] * 100:.2f} %</b></div></div>
{spark(acc["curve"])}
<div class="tablewrap"><table><tr><th>Cartera</th><th>Subcuenta</th><th>Desde la entrada</th><th>Posiciones</th><th>Órdenes</th><th></th></tr>{"".join(srows)}</table></div>
<details><summary>Eventos</summary><ul>{"".join(f"<li>{html.escape(e)}</li>" for e in acc["events"][:20])}</ul></details></div>''')
    orders = []
    for aid, name, _ in ACCS:
        for o in (st.get(aid) or {}).get("orders", [])[:60]:
            orders.append((o["t"], f'<tr><td>{fmt_time(o["t"])}</td><td>{"S2" if aid == "copy_s2" else "Azar"}</td><td>{o["w"][:8]}…</td>'
                                   f'<td>{o["coin"]}</td><td>{o["side"]}</td><td>{_f(o["usd"])} $'
                                   f'{" ⚠" if o["small"] else ""}</td><td>{_f(o["px"], 4)}</td></tr>'))
    orders.sort(reverse=True)
    s2, rnd = st.get("copy_s2"), st.get("copy_rnd")
    crit = ""
    if s2 and rnd:
        ok_a = s2["equity"] > CAPITAL; ok_b = s2["equity"] > rnd["equity"]; ok_c = s2["max_dd"] <= 0.30
        mark = lambda b: "✔" if b else "✘"
        crit = (f'<div class="ind">Evaluación del {EVAL_START} al {EVAL_END} (fijada de antemano). Hoy: '
                f'{mark(ok_a)} gana dinero · {mark(ok_b)} supera al control al azar · {mark(ok_c)} caída máx. ≤ 30 %'
                + (f' · BTC desde el inicio: {btc_r * 100:+.2f} %' if btc_r is not None else "") + '</div>')
    return f'''<div class="warn">Simulación con dinero ficticio. Cada minuto se leen las posiciones públicas de las carteras de Hyperliquid y se copian
a escala (1000 € por cuenta, 100 € por cartera, apalancamiento máx. 3×) en BTC, ETH, SOL, HYPE, XRP, ZEC, kPEPE, FARTCOIN y DOGE,
con comisión 0,045 % + deslizamiento 0,03 % por lado y funding. ⚠ = orden por debajo del mínimo real de Hyperliquid (10 $).
Selección {html.escape(str(sel.get("month")))} (datos hasta {html.escape(str(sel.get("datos_hasta")))}, {sel.get("n_copiables")} carteras copiables); se renueva el día 1 de cada mes.</div>
{crit}
<div class="tablewrap"><table class="summary"><tr><th>Cuenta</th><th>Capital</th><th>Resultado</th><th>Caída máx.</th><th>Órdenes</th><th>Órdenes/día</th><th>Bajo el mínimo</th></tr>{"".join(rows)}</table></div>
<h2 style="margin-top:22px">Detalle por cuenta</h2>
<div class="grid">{"".join(cards)}</div>
<h2 style="margin-top:22px">Últimas órdenes</h2>
<div class="tablewrap"><table><tr><th>Hora (Madrid)</th><th>Cuenta</th><th>Cartera</th><th>Moneda</th><th>Lado</th><th>Importe</th><th>Precio</th></tr>
{"".join(r for _, r in orders[:80]) or '<tr><td colspan="7" class="muted">Aún no hay órdenes</td></tr>'}</table></div>'''
