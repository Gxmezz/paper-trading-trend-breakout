"""
Estrategia SMC (Smart Money Concepts) para el oro, mecanizada a partir del curso en vídeo.

Reglas (ver la nota "Estrategia SMC oro (curso en video)" del vault):
  1. Sesgo en H1: estructura de mercado con swings fractales. Cierre por encima del último máximo
     confirmado -> alcista (BOS o CHoCH); por debajo del último mínimo -> bajista. Solo se opera a favor.
  2. Zonas en la temporalidad de entrada (M5/M15): tras un BOS/CHoCH a favor del sesgo, el impulso que rompe
     define un Order Block (última vela contraria antes del impulso). Válido solo si el impulso tiene fuerza
     (>= imp × ATR) y deja al menos un FVG. Modos de zona:
        ob     : el Order Block completo
        ob_ote : el Order Block, pero solo si al tocarlo está dentro de la zona OTE (61,8-78,6 %) del impulso
        fvg    : el último FVG del impulso
  3. Confirmación (no se entra al tocar):
        close  : tras tocar la zona, una vela cierra fuera de ella a favor (y con cuerpo a favor)
        brk    : tras tocar, el precio cierra por encima (compras) del máximo entre el toque y el mínimo hecho
                 en la zona (mini CHoCH)
     Si el precio cierra al otro lado de la zona antes de confirmar, la zona queda invalidada.
  4. Stop detrás del extremo de la zona / del mínimo del toque + sl × ATR. Hasta 3 parciales con objetivos
     en R; break even tras el TP1. El objetivo medio debe ser >= 2R (regla de riesgo 1:2).
  5. Filtro de noticias (opcional): sin entradas de 08:00 a 08:45 NY (datos de EE. UU. a las 08:30) ni en días
     de FOMC de 13:30 a 15:15 NY. 'close' además cierra antes del dato las posiciones que no estén en BE.
  Mejoras v2 (todas opcionales; por defecto se comporta como la versión 1):
     sweep   : el impulso debe nacer barriendo liquidez (su extremo supera el de las `sweep` velas anteriores)
     bias    : temporalidad del sesgo ("1h" o "4h"); ltf_align: además la estructura de la TF de entrada a favor
     filt    : filtro extra de tendencia: "ema" (EMA 200 de H1) o "sniper" (SMA 9 > SMA 21 de H1)
     pd      : comprar solo en descuento / vender solo en premium (respecto al 50 % del último rango de H1)
     sess    : (h0, h1) en UTC o "kz" (killzones de Londres 07-10 y Nueva York 12-16 UTC)
     tpm     : "R" (objetivos en R fijos) o "liq" (TP final en la liquidez de H1 más cercana; si queda a < 2R no
               se entra; TP1 = 1R, TP2 = punto medio)
     trail   : tras el TP1 el stop sigue al último mínimo/máximo confirmado de la TF de entrada
  6. Reglas de riesgo: 2 % por operación (posición en onzas enteras; si 1 oz arriesga más del máximo, se
     descarta), pérdida diaria máx. 5 % (no se abre si el riesgo no cabe), 4 pérdidas seguidas paran el día.
"""
import numpy as np, pandas as pd
from zoneinfo import ZoneInfo

EURUSD = 1.12
CAP = 1000 * EURUSD
STEP = 1.0
MAX_LEV = 50.0
SWAP_LONG = 0.06
NY = ZoneInfo("America/New_York")

# Días de decisión del FOMC (anuncio 14:00 NY)
FOMC = {"2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"}


def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


def structure(h, l, c, n):
    """Tendencia por estructura. Devuelve trend[i] (estado tras cerrar la vela i) y la lista de rupturas
    (i, dir, idx_swing_roto). Un swing en j se confirma en j+n (sin mirar al futuro)."""
    N = len(c); trend = np.zeros(N, dtype=np.int8); breaks = []
    sh = sl = None; shi = sli = -1; t = 0; used_h = used_l = -1
    for i in range(N):
        j = i - n
        if j >= n:
            if h[j] == max(h[j - n:i + 1]): sh, shi = h[j], j
            if l[j] == min(l[j - n:i + 1]): sl, sli = l[j], j
        if sh is not None and c[i] > sh and shi != used_h:
            t = 1; used_h = shi; breaks.append((i, 1, shi))
        elif sl is not None and c[i] < sl and sli != used_l:
            t = -1; used_l = sli; breaks.append((i, -1, sli))
        trend[i] = t
    return trend, breaks


def htf_trend(df, rule, n):
    """Tendencia de estructura de una temporalidad superior, disponible solo tras cerrar su vela."""
    hd = df.resample(rule).agg({"High": "max", "Low": "min", "Close": "last"}).dropna()
    tr, _ = structure(hd.High.values, hd.Low.values, hd.Close.values, n)
    s = pd.Series(tr, index=hd.index).shift(1)
    return s.reindex(df.index, method="ffill").fillna(0).values.astype(np.int8)


def build_zones(o, h, l, c, a, breaks, imp, zone_mode, sweep=0):
    """Zonas válidas creadas en cada ruptura de estructura de la temporalidad de entrada."""
    zones = []
    for i, d, js in breaks:
        if i - js < 2: continue
        seg = slice(js, i + 1)
        if d == 1:
            k0 = js + int(np.argmin(l[seg]))                       # inicio del impulso (mínimo tras el swing)
            leg_lo, leg_hi = l[k0], h[k0:i + 1].max()
        else:
            k0 = js + int(np.argmax(h[seg]))
            leg_lo, leg_hi = l[k0:i + 1].min(), h[k0]
        if leg_hi - leg_lo < imp * a[i]: continue                  # 1) fuerza
        if sweep and k0 > 0:                                       # barrida de liquidez en el origen del impulso
            a0 = max(0, k0 - sweep)
            if d == 1 and not l[k0] < l[a0:k0].min(): continue
            if d == -1 and not h[k0] > h[a0:k0].max(): continue
        fvg = None                                                 # 3) FVG en el impulso
        for t in range(max(k0 + 2, 2), i + 1):
            if d == 1 and l[t] > h[t - 2]: fvg = (h[t - 2], l[t])
            if d == -1 and h[t] < l[t - 2]: fvg = (h[t], l[t - 2])
        if fvg is None: continue
        ob = None                                                  # última vela contraria antes del impulso
        for t in range(k0, max(k0 - 6, -1), -1):
            if (d == 1 and c[t] < o[t]) or (d == -1 and c[t] > o[t]): ob = t; break
        if ob is None: ob = k0
        if zone_mode == "fvg": zlo, zhi = fvg
        else: zlo, zhi = l[ob], h[ob]
        if d == 1: zlo = min(zlo, leg_lo) if zone_mode != "fvg" else zlo
        else: zhi = max(zhi, leg_hi) if zone_mode != "fvg" else zhi
        zones.append(dict(born=i, d=d, lo=zlo, hi=zhi, leg_lo=leg_lo, leg_hi=leg_hi))
    return zones


def news_masks(idx, fomc_extra=True):
    """block[i]: no abrir. flat[i]: cerrar posiciones sin BE (vela inmediatamente anterior al dato)."""
    ny = idx.tz_convert(NY)
    mins = ny.hour * 60 + ny.minute
    wd = ny.weekday
    date = ny.strftime("%Y-%m-%d")
    fomc = np.isin(date, list(FOMC))
    data_win = (wd < 5) & (mins >= 8 * 60) & (mins < 8 * 60 + 45)
    fomc_win = fomc & (mins >= 13 * 60 + 30) & (mins < 15 * 60 + 15)
    block = np.asarray(data_win | fomc_win)
    flat = np.asarray(((wd < 5) & (mins >= 8 * 60 + 15) & (mins < 8 * 60 + 30)) |
                      (fomc & (mins >= 13 * 60 + 45) & (mins < 14 * 60)))
    return block, flat


def swings(h, l, n):
    """Swings fractales: listas (índice de confirmación, valor) de máximos y mínimos."""
    H, L = [], []
    for i in range(2 * n, len(h)):
        j = i - n
        if h[j] == max(h[j - n:i + 1]): H.append((i, h[j]))
        if l[j] == min(l[j - n:i + 1]): L.append((i, l[j]))
    return H, L


def last_level(sw, N):
    """Valor del último swing confirmado en cada vela."""
    out = np.full(N, np.nan); k = 0; v = np.nan
    for i in range(N):
        while k < len(sw) and sw[k][0] <= i: v = sw[k][1]; k += 1
        out[i] = v
    return out


def prepare(df, p):
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    N = len(c)
    a = atr(h, l, c)
    ltf_tr, breaks = structure(h, l, c, p["n"])
    zones = build_zones(o, h, l, c, a, breaks, p["imp"], p["zone"], p.get("sweep", 0))
    bias = htf_trend(df, p.get("bias", "1h"), p["nh"])
    if p.get("ltf_align"): bias = np.where(ltf_tr == bias, bias, 0).astype(np.int8)
    filt = p.get("filt") or ("ema" if p.get("ema_filter") else None)
    ema_ok = None
    hc = df.Close.resample("1h").last().dropna()
    if filt == "ema":
        e = hc.ewm(span=200, adjust=False).mean().shift(1).reindex(df.index, method="ffill").values
        ema_ok = np.where(c > e, 1, np.where(c < e, -1, 0))
    elif filt == "sniper":
        t = np.sign(hc.rolling(9).mean() - hc.rolling(21).mean()).shift(1)
        ema_ok = t.reindex(df.index, method="ffill").fillna(0).values
    # swings de H1 (rango premium/descuento y liquidez para objetivos), disponibles al cerrar su vela
    hd = df.resample("1h").agg({"High": "max", "Low": "min"}).dropna()
    HH, HL = swings(hd.High.values, hd.Low.values, p["nh"])
    def to_ltf(sw):   # índice de la primera vela de la TF de entrada que empieza tras cerrar la H1 del swing
        return [(int(df.index.searchsorted(hd.index[k] + pd.Timedelta(hours=1), side="left")), v) for k, v in sw]
    h1_hi, h1_lo = to_ltf(HH), to_ltf(HL)
    LH, LL = swings(h, l, p["n"])
    block, flat = news_masks(df.index)
    return dict(o=o, h=h, l=l, c=c, a=a, zones=zones, bias=bias, ema=ema_ok, block=block, flat=flat,
                h1_hi=h1_hi, h1_lo=h1_lo, rng_hi=last_level(h1_hi, N), rng_lo=last_level(h1_lo, N),
                ltf_hi=last_level(LH, N), ltf_lo=last_level(LL, N))


def liq_target(sw, i, px, d, look=12):
    """Liquidez de H1 más cercana más allá del precio (entre los últimos `look` swings confirmados)."""
    k = int(np.searchsorted([x[0] for x in sw], i, side="right"))
    vals = [v for _, v in sw[max(0, k - look):k] if (v > px if d == 1 else v < px)]
    if not vals: return None
    return min(vals) if d == 1 else max(vals)


def simulate(df, i0, p, pre=None, cost_side=0.20, risk=0.02, max_risk=0.02, daily_max=0.05, max_losses=4,
             curve=None, trades_out=None, state_out=None):
    """state_out (dict opcional): al terminar recibe la posición abierta (pos, entry, stop, parts, be, t_open, risk_usd)
    y el capital (eq, cur) para la cuenta en vivo del paper trading. No cambia los resultados."""
    pre = pre or prepare(df, p)
    o, h, l, c, a = pre["o"], pre["h"], pre["l"], pre["c"], pre["a"]
    bias, ema_ok, block, flat = pre["bias"], pre["ema"], pre["block"], pre["flat"]
    news = p.get("news", "off")
    hours = df.index.hour.values
    sess = p.get("sess", (0, 24))
    if sess == "kz": in_sess = ((hours >= 7) & (hours < 10)) | ((hours >= 12) & (hours < 16))
    else: in_sess = (hours >= sess[0]) & (hours < sess[1])
    tps0 = p["tp"]; tpm = p.get("tpm", "R"); trail = p.get("trail", False); use_pd = p.get("pd", False)
    rday = (df.index - pd.Timedelta(hours=22)).normalize().asi8
    wdy = (df.index - pd.Timedelta(hours=22)).weekday.values
    zones = sorted(pre["zones"], key=lambda z: z["born"])
    zi = 0; active = []
    eq = CAP; peak = CAP; mdd = 0.0
    pos = 0; parts = []; stop = 0.0; entry = 0.0; be_done = False; trade_pnl = 0.0; risk_usd = 0.0; t_open = None
    pend = None
    day_start = eq; day_losses = 0; cur_day = None
    ntr = 0; gp = gl = 0.0; skipped = 0; wins = 0; q_open = 0.0
    N = len(c)

    def close_all(px, i, reason):
        nonlocal eq, pos, parts, trade_pnl, ntr, gp, gl, day_losses, wins
        for q, tgt in parts:
            pnl = pos * q * (px - pos * cost_side - entry)
            eq += pnl; trade_pnl += pnl
        ntr += 1
        if trade_pnl > 0: gp += trade_pnl; wins += 1
        else: gl += -trade_pnl; day_losses += 1
        if trades_out is not None:
            trades_out.append(dict(t_in=df.index[t_open], t_out=df.index[i], side=pos, entry=entry,
                                   pnl_eur=trade_pnl / EURUSD, R=trade_pnl / risk_usd if risk_usd else 0, reason=reason,
                                   qty=float(q_open), exit=float(px)))
        pos = 0; parts = []; trade_pnl = 0.0

    for i in range(max(i0, 1), N):
        if rday[i] != cur_day:
            cur_day = rday[i]; day_start = eq; day_losses = 0
        # --- ejecutar entrada pendiente al open
        if pend is not None and pos == 0:
            d, sl_px = pend; pend = None
            px = o[i] + d * cost_side
            dist = (px - sl_px) * d
            tps = tps0
            if dist > 0 and tpm == "liq":
                lv = liq_target(pre["h1_hi"] if d == 1 else pre["h1_lo"], i - 1, px, d)
                Lr = (lv - px) * d / dist if lv is not None else 0
                if Lr < 2: dist = 0; skipped += 1
                else: Lr = min(Lr, 8.0); tps = (1.0, (1.0 + Lr) / 2, Lr)
            if dist > 0:
                q = np.floor(eq * risk / dist / STEP) * STEP
                if q < STEP: q = STEP if STEP * dist <= eq * max_risk else 0
                day_loss = (day_start - eq) / day_start
                if q and day_loss + q * dist / eq > daily_max + 1e-9: q = 0
                if q and q * px > eq * MAX_LEV: q = np.floor(eq * MAX_LEV / px / STEP) * STEP
                if q >= STEP:
                    n_parts = int(min(len(tps), q))
                    base = np.floor(q / n_parts); rest = q - base * n_parts
                    sel = {1: [len(tps) // 2], 2: [0, len(tps) - 1]}.get(n_parts, list(range(len(tps))))
                    parts = []
                    for k, ti in enumerate(sel):
                        qq = base + (1 if k < rest else 0)
                        parts.append([qq, px + d * tps[ti] * dist])
                    pos = d; entry = px; stop = sl_px; be_done = False; risk_usd = q * dist; t_open = i; q_open = q
                else:
                    skipped += 1
        # --- gestión de la posición
        if pos:
            if rday[i] != rday[i - 1] and pos == 1:
                nights = 3 if wdy[i] == 2 else 1
                sw = sum(q for q, _ in parts) * o[i] * SWAP_LONG / 365 * nights
                eq -= sw; trade_pnl -= sw
            hit_stop = l[i] <= stop if pos == 1 else h[i] >= stop
            if hit_stop:
                px = min(o[i], stop) if pos == 1 else max(o[i], stop)
                close_all(px, i, "stop" if not be_done else "BE")
            else:
                keep = []
                for q, tgt in parts:
                    if (pos == 1 and h[i] >= tgt) or (pos == -1 and l[i] <= tgt):
                        pnl = pos * q * (tgt - pos * cost_side - entry); eq += pnl; trade_pnl += pnl
                        if not be_done and p.get("be", True):
                            stop = entry; be_done = True
                    else:
                        keep.append([q, tgt])
                parts = keep
                if not parts:
                    pos_ = pos; parts = [[0, 0]]; close_all(c[i], i, "TP")
                elif news == "close" and flat[i] and not be_done:
                    close_all(c[i], i, "noticia")
                elif trail and be_done:
                    lv = pre["ltf_lo"][i] if pos == 1 else pre["ltf_hi"][i]
                    if lv == lv: stop = max(stop, lv) if pos == 1 else min(stop, lv)
        cur = eq + (sum(pos * q * (c[i] - entry) for q, _ in parts) if pos else 0)
        peak = max(peak, cur); mdd = min(mdd, cur / peak - 1)
        if curve is not None: curve.append(cur / EURUSD)
        if cur < CAP * 0.1: break
        # --- zonas
        while zi < len(zones) and zones[zi]["born"] <= i:
            z = dict(zones[zi]); z["touch"] = -1; z["ext"] = None; z["mx"] = None; active.append(z); zi += 1
        alive = []
        for z in active:
            if i <= z["born"] or i - z["born"] > p["age"]:
                if i <= z["born"]: alive.append(z)
                continue
            d = z["d"]
            if d == 1: z["leg_hi"] = max(z["leg_hi"], h[i]) if z["touch"] < 0 else z["leg_hi"]
            else: z["leg_lo"] = min(z["leg_lo"], l[i]) if z["touch"] < 0 else z["leg_lo"]
            touched_now = (l[i] <= z["hi"]) if d == 1 else (h[i] >= z["lo"])
            if z["touch"] < 0:
                if not touched_now: alive.append(z); continue
                if p["zone"] == "ob_ote":
                    rng = z["leg_hi"] - z["leg_lo"]
                    if d == 1: ote_lo, ote_hi = z["leg_hi"] - 0.786 * rng, z["leg_hi"] - 0.618 * rng
                    else: ote_lo, ote_hi = z["leg_lo"] + 0.618 * rng, z["leg_lo"] + 0.786 * rng
                    if z["hi"] < ote_lo or z["lo"] > ote_hi: continue        # OB fuera de la OTE: se descarta
                z["touch"] = i; z["ext"] = l[i] if d == 1 else h[i]; z["mx"] = h[i] if d == 1 else l[i]
            else:
                if d == 1:
                    if l[i] < z["ext"]: z["ext"] = l[i]; z["mx"] = h[i]
                else:
                    if h[i] > z["ext"]: z["ext"] = h[i]; z["mx"] = l[i]
            # invalidación: cierre al otro lado
            if (d == 1 and c[i] < z["lo"]) or (d == -1 and c[i] > z["hi"]): continue
            if i - z["touch"] > p["conf_win"]: continue
            if p["conf"] == "close":
                ok = (c[i] > z["hi"] and c[i] > o[i]) if d == 1 else (c[i] < z["lo"] and c[i] < o[i])
            else:
                ok = (i > z["touch"] and c[i] > z["mx"] and c[i] > o[i]) if d == 1 else \
                     (i > z["touch"] and c[i] < z["mx"] and c[i] < o[i])
                if not ok:
                    if d == 1: z["mx"] = max(z["mx"], h[i]) if l[i] > z["ext"] else z["mx"]
                    else: z["mx"] = min(z["mx"], l[i]) if h[i] < z["ext"] else z["mx"]
            if not ok: alive.append(z); continue
            # señal confirmada: comprobar filtros
            if use_pd:
                rh, rl = pre["rng_hi"][i], pre["rng_lo"][i]
                mid = (rh + rl) / 2 if rh == rh and rl == rl else None
                pd_ok = mid is not None and ((c[i] < mid) if d == 1 else (c[i] > mid))
            else: pd_ok = True
            can = (pos == 0 and pend is None and i + 1 < N and bias[i] == d and in_sess[i] and pd_ok
                   and day_losses < max_losses and (ema_ok is None or ema_ok[i] == d)
                   and not (news != "off" and (block[i] or (i + 1 < N and block[i + 1]))))
            if can:
                sl_px = (min(z["lo"], z["ext"]) - p["sl"] * a[i]) if d == 1 else (max(z["hi"], z["ext"]) + p["sl"] * a[i])
                pend = (d, sl_px)
            # la zona se consume tanto si se entra como si no
        active = alive
    if state_out is not None:
        state_out.update(pos=int(pos), entry=float(entry), stop=float(stop), parts=[[float(q), float(t)] for q, t in parts],
                         be=bool(be_done), t_open=(df.index[t_open] if pos and t_open is not None else None),
                         risk_usd=float(risk_usd), eq=float(eq), cur=float(cur), pending=pend is not None)
    days = (df.index[-1] - df.index[i0]).days or 1
    ret = cur / CAP - 1
    return dict(trades=ntr, win=round(wins / ntr * 100) if ntr else 0,
                PF=round(gp / gl, 2) if gl else (99.0 if gp else 0.0), ret=round(ret * 100, 1),
                monthly=round(((1 + ret) ** (30.4 / days) - 1) * 100, 2) if ret > -1 else -100,
                maxDD=round(mdd * 100, 1), skipped=skipped)
