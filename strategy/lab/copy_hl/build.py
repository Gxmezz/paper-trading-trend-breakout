"""Construye, para cada cartera de la muestra:
  - P[h]: beneficio acumulado de su COPIA por cada 1 $ de base (retraso 60 s, comisión,
    deslizamiento, funding y apalancamiento máx. 3x), al cierre de cada hora UTC.
  - G[h]: exposición bruta de la copia (para el coste de entrar y salir).
  - Métricas semanales de estilo de la cartera original (órdenes, apalancamiento,
    duración de las posiciones, moneda principal, % de volumen copiable).

Uso:
  python strategy/lab/copy_hl/build.py coins    -> coins.json (lista de monedas copiables)
  python strategy/lab/copy_hl/build.py series   -> series.pkl
"""
import collections, glob, json, math, os, pickle, sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DATA = Path(os.environ.get("COPYHL_DATA", ROOT / "strategy" / "data" / "hl"))
PXD = DATA / "px"

H0 = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp() * 1000)  # lunes
# fin de los datos: fijo para el examen; la selección mensual en vivo lo cambia con COPYHL_END
END = int(pd.Timestamp(os.environ.get("COPYHL_END", "2026-11-16"), tz="UTC").timestamp() * 1000)
MS_M, MS_H, MS_W = 60_000, 3_600_000, 7 * 86_400_000
NH = (END - H0) // MS_H
NW = (END - H0) // MS_W + 1
NM = (END - H0) // MS_M
DELAY = 60_000
COST = 0.00045 + 0.0003  # comisión taker + deslizamiento, por lado
MAXLEV = 3.0


def perp(c):
    return not (c.startswith("@") or "/" in c)


def wallets():
    return sorted(glob.glob(str(DATA / "wallets" / "*.pkl")))


# ---------------------------------------------------------------- monedas
def build_coins():
    import requests
    vol = collections.Counter()
    for f in wallets():
        for x in pickle.load(open(f, "rb"))["fills"]:
            if perp(x["coin"]) and ":" not in x["coin"]:
                vol[x["coin"]] += float(x["px"]) * float(x["sz"])
    info = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=60).json()
    binance = {s["symbol"] for s in info["symbols"]}
    from download_prices import bsym
    tot, acc, out = sum(vol.values()), 0, []
    for c, v in vol.most_common():
        if acc / tot >= 0.90:
            break
        acc += v
        if bsym(c) in binance:
            out.append(c)
    (DATA / "coins.json").write_text(json.dumps(out))
    print(len(out), "monedas copiables:", out)


# ---------------------------------------------------------------- precios
def load_prices(coins):
    px = {}
    for c in coins:
        f = PXD / f"{c}.pkl"
        if not f.exists():
            continue
        df = pickle.load(open(f, "rb"))
        m = ((df.index.values - H0) // MS_M).astype(np.int64)
        ok = (m >= 0) & (m < NM)
        o = np.full(NM, np.nan, np.float32); cl = np.full(NM, np.nan, np.float32)
        o[m[ok]] = df["o"].values[ok]; cl[m[ok]] = df["c"].values[ok]
        cl = pd.Series(cl).ffill().values.astype(np.float32)
        o = np.where(np.isnan(o), np.r_[np.nan, cl[:-1]], o).astype(np.float32)
        hc = cl[59::60][:NH]
        fr = np.zeros(NH, np.float64)
        ff = PXD / f"{c}_funding.pkl"
        if ff.exists():
            s = pickle.load(open(ff, "rb"))
            hh = ((s.index.values - H0) // MS_H).astype(np.int64)
            k = (hh >= 0) & (hh < NH)
            fr[hh[k]] = s.values[k]
        px[c] = (o, hc, fr)
    return px


# ---------------------------------------------------------------- cartera
def av_lookup(port):
    a = np.array([[x[0], float(x[1])] for x in port["accountValueHistory"]]) if port else np.zeros((0, 2))
    return a[:, 0], a[:, 1]


def process(d, px, coins):
    F = sorted([x for x in d["fills"] if perp(x["coin"])], key=lambda x: (x["time"], x["tid"]))
    if not F:
        return None
    avt, avv = av_lookup(d["portfolio"])

    def AV(t):
        i = np.searchsorted(avt, t, side="right") - 1
        return avv[i] if i >= 0 else (avv[0] if len(avv) else np.nan)

    # --- estilo semanal de la cartera original
    n_ord = np.zeros(NW); notl = np.zeros(NW); wl = np.zeros(NW); lev = np.zeros(NW)
    hold_sum = np.zeros(NW); hold_n = np.zeros(NW)
    coinv = collections.defaultdict(lambda: np.zeros(NW))
    holds = []  # (t_fin, duración en min)
    pos, lpx, opened, oids = {}, {}, {}, set()
    for x in F:
        t, c = x["time"], x["coin"]
        w = (t - H0) // MS_W
        if w < 0 or w >= NW:
            continue
        p, sz = float(x["px"]), float(x["sz"])
        new = float(x["startPosition"]) + (sz if x["side"] == "B" else -sz)
        old = pos.get(c, float(x["startPosition"]))
        if (w, x["oid"]) not in oids:
            oids.add((w, x["oid"])); n_ord[w] += 1
        v = p * sz
        notl[w] += v; coinv[c][w] += v
        if c in coins:
            wl[w] += v
        eps = 1e-12
        if abs(old) > eps and (abs(new) <= eps or np.sign(new) != np.sign(old)):
            if c in opened:
                dur = (t - opened.pop(c)) / MS_M
                holds.append((t, dur)); hold_sum[w] += dur; hold_n[w] += 1
        if abs(new) > eps and (abs(old) <= eps or np.sign(new) != np.sign(old)):
            opened[c] = t
        pos[c], lpx[c] = new, p
        a = AV(t)
        if a and a > 0:
            lev[w] = max(lev[w], sum(abs(pos[k] * lpx[k]) for k in pos) / a)
    topc = {}
    for c, arr in coinv.items():
        topc[c] = arr.astype(np.float32)

    # --- copia simulada
    ev = []  # (minuto de ejecución, moneda, posición objetivo de la cartera, AV)
    for x in F:
        c = x["coin"]
        if c not in px:
            continue
        sz = float(x["sz"])
        new = float(x["startPosition"]) + (sz if x["side"] == "B" else -sz)
        em = (x["time"] - H0 + DELAY + MS_M - 1) // MS_M
        if em < 0 or em >= NM:
            continue
        if ev and ev[-1][0] == em and ev[-1][1] == c:
            ev[-1] = (em, c, new, AV(x["time"]))
        else:
            ev.append((em, c, new, AV(x["time"])))
    ev.sort(key=lambda e: e[0])
    q, lp = collections.defaultdict(float), {}
    trades = []  # (hora, moneda, dq, flujo de caja)
    for em, c, new, a in ev:
        p = float(px[c][0][em])
        if not np.isfinite(p) or p <= 0:
            continue
        a = a if (a is not None and np.isfinite(a) and a > 1) else 1.0
        tgt = new / a
        other = sum(abs(q[k] * lp[k]) for k in q if k != c and k in lp)
        room = max(MAXLEV - other, 0.0)
        if abs(tgt * p) > room:
            keep = abs(q[c]) if np.sign(q[c]) == np.sign(tgt) else 0.0
            tgt = math.copysign(min(abs(tgt), max(room / p, keep)), tgt)
        dq = tgt - q[c]
        lp[c] = p
        if abs(dq * p) < 1e-7:
            continue
        q[c] = tgt
        trades.append((em // 60, c, dq, -dq * p - abs(dq * p) * COST))
    if not trades:
        return dict(addr=d["addr"], h0=None, n_ord=n_ord, notl=notl, wl=wl, lev=lev,
                    hold_sum=hold_sum, hold_n=hold_n, holds=np.array(holds), topc=topc)
    hs = trades[0][0]
    L = NH - hs
    cash = np.zeros(L); P = np.zeros(L); G = np.zeros(L)
    per = collections.defaultdict(lambda: np.zeros(L))
    for h, c, dq, cf in trades:
        cash[h - hs] += cf; per[c][h - hs] += dq
    for c, dqa in per.items():
        qe = np.cumsum(dqa)
        hc = px[c][1][hs:].astype(np.float64)
        hc = pd.Series(hc).ffill().fillna(0).values
        val = qe * hc
        P += val; G += np.abs(val)
        # funding al inicio de la hora h con la posición al final de h-1 (largo paga si es +)
        fr = px[c][2][hs:]
        fund = np.zeros(L); fund[:-1] = -qe[:-1] * hc[:-1] * fr[1:]
        cash += fund
    P += np.cumsum(cash)
    return dict(addr=d["addr"], h0=hs, P=P.astype(np.float32),
                trade_h=np.array([tr[0] for tr in trades], np.int32), G=G.astype(np.float32),
                n_ord=n_ord, notl=notl, wl=wl, lev=lev, hold_sum=hold_sum, hold_n=hold_n,
                holds=np.array(holds), topc=topc, n_fills=len(F),
                first_fill=F[0]["time"], truncated=len(d["fills"]) >= 9900)


def build_series():
    coins = set(json.loads((DATA / "coins.json").read_text()))
    px = load_prices(coins)
    print("precios cargados:", sorted(px))
    out = []
    for i, f in enumerate(wallets()):
        r = process(pickle.load(open(f, "rb")), px, coins)
        if r:
            out.append(r)
        if i % 100 == 0:
            print(i, flush=True)
    btc = px["BTC"][1]
    pickle.dump({"wallets": out, "btc": btc, "coins": sorted(px)}, open(DATA / "series.pkl", "wb"))
    print("carteras:", len(out), "con copia:", sum(r["h0"] is not None for r in out))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    {"coins": build_coins, "series": build_series}[sys.argv[1]]()
