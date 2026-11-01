"""
خدمة تنبيهات رادار السيولة — تفحص الأسهم كل دقيقة وقت السوق وترسل تنبيهات تلقرام
"""
import os
import sys
import csv
import json
import time
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

TOKEN = os.environ["TG_TOKEN"]
CHAT = os.environ["TG_CHAT_ID"]
ALERT_MIN = int(os.environ.get("ALERT_MIN_SCORE", "7"))
STATE_DIR = os.environ.get("STATE_DIR", "/opt/radar/state")
os.makedirs(STATE_DIR, exist_ok=True)

NY = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")
MARKET = "SPY"

# نفس الإعدادات الافتراضية في الرادار
flow_len, rvol_min, spike_min = 15, 2.0, 3.0
in_min, out_max = 65, 35
near_pct, near_tl = 1.0, 0.4
atr_mult, rs_min, mom_min = 0.5, 1.0, 0.5
min_score, min_touch = 7, 3
opt_budget, opt_dte, opt_min_oi = (100, 200), (5, 21), 100

DEFAULT_TICKERS = (
    "AAPL MSFT NVDA AMD TSLA META AMZN GOOGL NFLX AVGO PLTR SMCI COIN MSTR SOFI "
    "UBER SHOP CRWD SNOW PANW MU INTC ARM MRVL RIVN LCID NIO BABA PYPL XYZ "
    "ROKU DKNG HOOD RBLX AFRM UPST MARA RIOT CVNA ENPH"
)


def pick(df, sym):
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        if sym in df.columns.get_level_values(0):
            d = df[sym]
        elif sym in df.columns.get_level_values(1):
            d = df.xs(sym, axis=1, level=1)
        else:
            return None
    else:
        d = df
    d = d.dropna(subset=["Close"])
    return d if len(d) else None


def money_flow(d):
    h, l, c, v = d["High"], d["Low"], d["Close"], d["Volume"]
    rng = (h - l).replace(0, np.nan)
    mfm = (((c - l) - (h - c)) / rng).fillna(0)
    return mfm * v * c


def vwap_of(d):
    typical = (d["High"] + d["Low"] + d["Close"]) / 3
    return (typical * d["Volume"]).cumsum() / d["Volume"].cumsum()


def split_days(intr, daily):
    """يرجع شموع اليوم الأخير والأيام السابقة فقط."""
    if intr is None or daily is None:
        return None, None
    day = intr.index[-1].date()
    return intr[intr.index.date == day], daily[daily.index.date < day]


def market_state(intr, daily):
    today, past = split_days(intr, daily)
    if today is None or past is None or len(today) < 5 or len(past) < 2:
        return {"chg": 0.0, "above_vwap": None}
    price = float(today["Close"].iloc[-1])
    return {"chg": (price / float(past["Close"].iloc[-1]) - 1) * 100,
            "above_vwap": price > float(vwap_of(today).iloc[-1])}


def find_trendline(bars, kind, k=3, lookback=130, min_age=8, min_tol=0.002):
    """خط ترند صالح على شموع مكتملة: res = هابط يربط القمم، sup = صاعد يربط القيعان.
    الخط صالح فقط إذا ما فيه شمعة اخترقته (ولا حتى بالذيل) من أول لمسة لين آخر شمعة مكتملة."""
    d = bars.tail(lookback)
    n = len(d)
    if n < 30:
        return None
    hi, lo = d["High"].to_numpy(float), d["Low"].to_numpy(float)
    cl = d["Close"].to_numpy(float)
    atr_pct = float(np.mean(((hi - lo) / cl)[-14:])) * 100
    src = hi if kind == "res" else lo
    piv = [i for i in range(k, n - k)
           if src[i] == (src[i - k:i + k + 1].max() if kind == "res" else src[i - k:i + k + 1].min())]
    tol = max(min_tol, 0.25 * atr_pct / 100)
    best = None
    for a, i1 in enumerate(piv):
        if n - 1 - i1 < min_age:
            continue
        for i2 in piv[a + 1:]:
            if i2 - i1 < 5:
                continue
            y1, y2 = src[i1], src[i2]
            if (kind == "res" and y2 >= y1) or (kind == "sup" and y2 <= y1):
                continue
            slope = (y2 - y1) / (i2 - i1)
            line = y1 + slope * (np.arange(i1, n) - i1)
            if kind == "res" and np.any(hi[i1:] > line * (1 + tol)):
                continue
            if kind == "sup" and np.any(lo[i1:] < line * (1 - tol)):
                continue
            touches = sum(abs(src[p] - (y1 + slope * (p - i1))) / (y1 + slope * (p - i1)) <= tol
                          for p in piv if p >= i1)
            today_val = y1 + slope * (n - i1)
            if today_val <= 0:
                continue
            cand = dict(i1=i1, y1=y1, slope=slope, touches=int(touches), val=today_val,
                        prev_val=y1 + slope * (n - 1 - i1), start=d.index[i1], n=n)
            if best is None or touches > best["touches"] or (touches == best["touches"] and (
                    today_val < best["val"] if kind == "res" else today_val > best["val"])):
                best = cand
    return best


def completed(bars, minutes, now_ts):
    """الشموع المكتملة فقط (يستبعد الشمعة اللي للحين ما سكرت)."""
    if bars is None:
        return None
    cb = bars[bars.index + pd.Timedelta(minutes=minutes) <= now_ts]
    return cb if len(cb) else None


def analyze(sym, intr, daily, mkt, b15, b60):
    today, past = split_days(intr, daily)
    if today is None or past is None or len(today) < 10 or len(past) < 21:
        return None
    c, v = today["Close"], today["Volume"]
    price = float(c.iloc[-1])
    prev_close = float(past["Close"].iloc[-1])
    chg = (price / prev_close - 1) * 100

    # حجم غير معتاد
    avg_vol = float(past["Volume"].tail(20).mean())
    elapsed = (today.index[-1] - today.index[0]).total_seconds() / 60 + 1
    frac = min(max(elapsed / 390, 0.01), 1.0)
    rvol = v.sum() / (avg_vol * frac) if avg_vol > 0 else 0
    base = v.iloc[:-5].tail(60).mean() * 5
    spike = v.tail(5).sum() / base if base > 0 else 0

    # اتجاه السيولة
    mf = money_flow(today)
    w = mf.tail(flow_len)
    inflow, outflow = w[w > 0].sum(), -w[w < 0].sum()
    in_pct = inflow / (inflow + outflow) * 100 if inflow + outflow > 0 else 50

    # الزخم و VWAP
    ref = c.iloc[-flow_len - 1] if len(c) > flow_len else c.iloc[0]
    mom = (price / ref - 1) * 100
    vwap = float(vwap_of(today).iloc[-1])

    # متوسط 20 يوم (قبل اليوم وباحتساب السعر الحالي)
    closes = past["Close"]
    sma_prev = float(closes.tail(20).mean())
    sma_live = float((closes.tail(19).sum() + price) / 20)
    dist = (price / sma_live - 1) * 100
    cross_up = prev_close < sma_prev and price > sma_live
    near_up = price <= sma_live and -dist <= near_pct and mom > 0
    cross_dn = prev_close > sma_prev and price < sma_live
    near_dn = price >= sma_live and dist <= near_pct and mom < 0

    # الحركة مقابل الحركة الطبيعية (ATR 14)
    h, l, pc = past["High"], past["Low"], past["Close"].shift()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr_pct = float(tr.tail(14).mean()) / prev_close * 100
    move = chg / atr_pct if atr_pct > 0 else 0

    # السوق
    rs = chg - mkt["chg"]
    mkt_up = mkt["above_vwap"] is True and mkt["chg"] > 0
    mkt_dn = mkt["above_vwap"] is False and mkt["chg"] < 0

    # خطوط الترند على فريم 15 دقيقة والساعة
    now_ts = today.index[-1] + pd.Timedelta(minutes=1)
    tl = {}
    for name, bars, mins in (("15د", b15, 15), ("ساعة", b60, 60)):
        cb = completed(bars, mins, now_ts)
        res = sup = None
        if cb is not None and len(cb) >= 30:
            res = find_trendline(cb, "res")
            sup = find_trendline(cb, "sup")
            res = res if res and res["touches"] >= min_touch else None
            sup = sup if sup and sup["touches"] >= min_touch else None
        last_c = float(cb["Close"].iloc[-1]) if cb is not None else price
        up_x = bool(res) and last_c < res["prev_val"] and price > res["val"]
        up_n = bool(res) and price <= res["val"] and (res["val"] / price - 1) * 100 <= near_tl and mom > 0
        dn_x = bool(sup) and last_c > sup["prev_val"] and price < sup["val"]
        dn_n = bool(sup) and price >= sup["val"] and (1 - sup["val"] / price) * 100 <= near_tl and mom < 0
        tl[name] = dict(res=res, sup=sup, up_x=up_x, up_n=up_n, dn_x=dn_x, dn_n=dn_n)

    def trend_detail(line, cross, near, word):
        if not line:
            return "ما فيه ترند واضح"
        t = f"{line['touches']} لمسات"
        if cross:
            return f"{word} الحين، {t}"
        gap = abs(price / line["val"] - 1) * 100
        return f"باقي {gap:.2f}%، {t}" if near else f"يبعد {gap:.1f}%"

    def ma_detail(cross, near, word):
        if cross:
            return f"{word} اليوم"
        if near:
            return f"باقي {abs(dist):.1f}%"
        return f"{dist:+.1f}%"

    bull = [
        ("سيولة داخلة قوية", in_pct >= in_min, f"{in_pct:.0f}%"),
        ("اختراق متوسط 20 يوم", cross_up or near_up, ma_detail(cross_up, near_up, "اخترق")),
        ("حجم غير معتاد", rvol >= rvol_min, f"{rvol:.1f}x"),
        ("فوق VWAP", price > vwap, ""),
        ("زخم صاعد", mom >= mom_min, f"{mom:+.2f}%"),
        ("حركة أكبر من الطبيعي", move >= atr_mult, f"{move:.1f}x"),
        ("أقوى من السوق", rs >= rs_min, f"{rs:+.1f}%"),
        ("السوق داعم", mkt_up, f"SPY {mkt['chg']:+.1f}%"),
    ] + [(f"اختراق ترند هابط {n}", tl[n]["up_x"] or tl[n]["up_n"],
           trend_detail(tl[n]["res"], tl[n]["up_x"], tl[n]["up_n"], "اخترق")) for n in ("15د", "ساعة")]
    bear = [
        ("سيولة خارجة قوية", in_pct <= out_max, f"{100 - in_pct:.0f}%"),
        ("كسر متوسط 20 يوم", cross_dn or near_dn, ma_detail(cross_dn, near_dn, "كسر")),
        ("حجم غير معتاد", rvol >= rvol_min, f"{rvol:.1f}x"),
        ("تحت VWAP", price < vwap, ""),
        ("زخم هابط", mom <= -mom_min, f"{mom:+.2f}%"),
        ("حركة أكبر من الطبيعي", move <= -atr_mult, f"{abs(move):.1f}x"),
        ("أضعف من السوق", -rs >= rs_min, f"{rs:+.1f}%"),
        ("السوق ضاغط", mkt_dn, f"SPY {mkt['chg']:+.1f}%"),
    ] + [(f"كسر ترند صاعد {n}", tl[n]["dn_x"] or tl[n]["dn_n"],
           trend_detail(tl[n]["sup"], tl[n]["dn_x"], tl[n]["dn_n"], "كسر")) for n in ("15د", "ساعة")]
    s_up, s_dn = sum(ok for _, ok, _ in bull), sum(ok for _, ok, _ in bear)
    side = "up" if s_up >= s_dn else "dn"
    checks, score = (bull, s_up) if side == "up" else (bear, s_dn)
    # الإشارة القوية تحتاج: النقاط الكافية + السيولة + المتوسط
    strong = score >= min_score and checks[0][1] and checks[1][1]
    accum = (rvol >= rvol_min and spike >= spike_min and abs(move) <= 0.3
             and abs(price / vwap - 1) * 100 <= 0.5)
    status = side if strong else ("acc" if accum else "")

    return dict(sym=sym, price=price, chg=chg, rvol=rvol, in_pct=in_pct,
                net_day=mf.sum(), mom=mom, sma=sma_live, tl=tl, side=side, score=score,
                checks=checks, status=status, unusual=rvol >= rvol_min,
                last=today.index[-1])


def _cached(store, key, ttl, fn):
    hit = store.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    store[key] = (time.time(), val)
    return val


def _fetch(fn, tries=3):
    """يعيد المحاولة إذا Yahoo رفض الطلب مؤقتاً (ضغط طلبات)."""
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def best_option(store, sym, kind, price, budget, dte_rng, min_oi):
    """أقرب عقد Out of the Money للسعر، سعره ضمن الميزانية، وفيه سيولة كافية.
    يرجع (العقد، السبب): ok / error / no_exp / no_match"""
    try:
        exps = _cached(store, (sym, "exp"), 3600, lambda: _fetch(lambda: list(yf.Ticker(sym).options)))
    except Exception:
        return None, "error"
    if not exps:
        return None, "error"
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    lo, hi = budget[0] / 100, budget[1] / 100
    in_range, errors = 0, 0
    for e in exps:
        dte = (pd.Timestamp(e) - today).days
        if dte < dte_rng[0]:
            continue
        if dte > dte_rng[1]:
            break
        in_range += 1
        try:
            ch = _cached(store, (sym, e), 60, lambda: _fetch(lambda: yf.Ticker(sym).option_chain(e)))
        except Exception:
            errors += 1
            continue
        df = (ch.calls if kind == "call" else ch.puts).copy()
        if df.empty:
            continue
        for col in ("bid", "ask", "lastPrice", "openInterest", "volume"):
            df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(0)
        bid, ask = df["bid"], df["ask"]
        live = (bid > 0) & (ask > 0)
        df["mid"] = np.where(live, (bid + ask) / 2, df["lastPrice"])
        df["spread"] = np.where(live, (ask - bid) / df["mid"].replace(0, np.nan), np.nan)
        df["liq"] = np.maximum(df["openInterest"], df["volume"])
        # Yahoo أحياناً يرجع العقود المفتوحة صفر للسلسلة كلها (خصوصاً خارج وقت السوق)
        liq_ok = (df["liq"] >= min_oi) if df["liq"].max() > 0 else True
        otm = df["strike"] > price if kind == "call" else df["strike"] < price
        ok = otm & df["mid"].between(lo, hi) & liq_ok & (df["spread"].isna() | (df["spread"] <= 0.25))
        c = df[ok]
        if c.empty:
            continue
        row = c.loc[(c["strike"] - price).abs().idxmin()]
        last_trade = pd.to_datetime(row.get("lastTradeDate"), errors="coerce", utc=True)
        return dict(kind=kind, strike=float(row["strike"]), exp=pd.Timestamp(e), dte=dte,
                    cost=float(row["mid"]) * 100, oi=int(row["openInterest"]),
                    iv=float(row.get("impliedVolatility") or 0) * 100, last=last_trade), "ok"
    if in_range == 0:
        return None, "no_exp"
    if errors == in_range:
        return None, "error"
    return None, "no_match"


def log(*a):
    print(dt.datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def send(text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          data={"chat_id": CHAT, "text": text}, timeout=20)
        if not r.ok:
            log("telegram error", r.status_code, r.text[:200])
    except Exception as e:
        log("telegram exception", e)


def dl(tickers, period, interval):
    return yf.download(list(tickers), period=period, interval=interval, group_by="ticker",
                       prepost=False, threads=4, progress=False, auto_adjust=False)


def market_open(now_ny):
    if now_ny.weekday() >= 5:
        return False
    t = now_ny.hour * 60 + now_ny.minute
    return 9 * 60 + 30 <= t < 16 * 60


def riyadh_time():
    t = dt.datetime.now(RIYADH)
    return t.strftime("%I:%M").lstrip("0") + (" م" if t.hour >= 12 else " ص")


def fmt(r, opt):
    up = r["side"] == "up"
    head = ("🟢 صعود" if up else "🔴 هبوط") + f" | {r['sym']}"
    lines = [head,
             f"السعر: ${r['price']:.2f}  ({r['chg']:+.2f}%)",
             f"النقاط: {r['score']}/10", ""]
    for name, ok, d in r["checks"]:
        lines.append(("✓ " if ok else "· ") + name + (f" {d}" if d else ""))
    lines.append("")
    if opt:
        lines.append(f"📄 {opt['kind'].upper()} ${opt['strike']:g} | {opt['exp'].strftime('%m/%d')} | ${opt['cost']:.0f}")
    else:
        lines.append("📄 ما فيه عقد مناسب ضمن الشروط")
    lines.append(f"⏰ {riyadh_time()} بتوقيت السعودية")
    lines.append("أسعار Yahoo قد تتأخر قليلاً، والعقد متأخر ~15 دقيقة")
    return "\n".join(lines)


def sent_path(day):
    return os.path.join(STATE_DIR, f"sent-{day}.json")


def load_sent(day):
    try:
        return set(json.load(open(sent_path(day))))
    except Exception:
        return set()


def save_sent(day, s):
    json.dump(sorted(s), open(sent_path(day), "w"))


def log_signal(r, opt):
    path = os.path.join(STATE_DIR, "signals.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_riyadh", "symbol", "side", "score", "price", "change_pct",
                        "option", "strike", "expiry", "option_cost"])
        w.writerow([dt.datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M"), r["sym"], r["side"], r["score"],
                    round(r["price"], 2), round(r["chg"], 2),
                    opt["kind"] if opt else "", opt["strike"] if opt else "",
                    opt["exp"].strftime("%Y-%m-%d") if opt else "", round(opt["cost"]) if opt else ""])


def scan(tickers, daily_all, store):
    fetch = tuple(dict.fromkeys(tuple(tickers) + (MARKET,)))
    intr_all = dl(fetch, "1d", "1m")
    b15_all = dl(fetch, "5d", "15m")
    b60_all = dl(fetch, "1mo", "60m")
    mkt = market_state(pick(intr_all, MARKET), pick(daily_all, MARKET))
    out = []
    for s in tickers:
        try:
            r = analyze(s, pick(intr_all, s), pick(daily_all, s), mkt, pick(b15_all, s), pick(b60_all, s))
        except Exception as e:
            log("analyze error", s, e)
            r = None
        if r:
            out.append(r)
    return out


def main():
    tickers = [t for t in DEFAULT_TICKERS.split() if t]
    extra = os.environ.get("TICKERS", "").split()
    if extra:
        tickers = extra
    log("starting, tickers:", len(tickers), "min score:", ALERT_MIN)
    send(f"✅ الرادار شغال على السيرفر\nيفحص {len(tickers)} سهم كل دقيقة وقت السوق، وينبهك عند {ALERT_MIN}/10 أو أكثر.")
    store, daily_all, daily_at = {}, None, 0
    while True:
        try:
            now_ny = dt.datetime.now(NY)
            if not market_open(now_ny):
                time.sleep(60)
                continue
            if daily_all is None or time.time() - daily_at > 3600:
                fetch = tuple(dict.fromkeys(tuple(tickers) + (MARKET,)))
                daily_all, daily_at = dl(fetch, "6mo", "1d"), time.time()
            day = now_ny.strftime("%Y-%m-%d")
            sent = load_sent(day)
            results = scan(tickers, daily_all, store)
            for r in results:
                key = f"{r['sym']}:{r['side']}"
                if r["score"] >= ALERT_MIN and key not in sent:
                    kind = "call" if r["side"] == "up" else "put"
                    try:
                        opt, _ = best_option(store, r["sym"], kind, r["price"], opt_budget, opt_dte, opt_min_oi)
                    except Exception:
                        opt = None
                    send(fmt(r, opt))
                    log_signal(r, opt)
                    sent.add(key)
                    save_sent(day, sent)
                    log("alert", key, r["score"])
            log("scan ok", len(results), "stocks")
        except Exception as e:
            log("loop error", e)
        time.sleep(max(5, 60 - dt.datetime.now().second))


if __name__ == "__main__":
    main()
