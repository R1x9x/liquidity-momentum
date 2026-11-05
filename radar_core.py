"""
نواة رادار السيولة: تحميل البيانات (Alpaca أو Yahoo)، نقاط الإشارة العشر، اختيار العقد، ومسح الأسهم الصغيرة.
يستخدمها الموقع (app.py) وخدمة التنبيهات (server/alerts.py) عشان الحساب يكون واحد في المكانين.
"""
import os
import math
import time
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

NY = ZoneInfo("America/New_York")
MARKET = "SPY"

DEFAULT_TICKERS = (
    "SPY AAPL MSFT NVDA AMD TSLA META AMZN GOOGL NFLX AVGO PLTR SMCI COIN MSTR SOFI "
    "UBER SHOP CRWD SNOW PANW MU INTC ARM MRVL RIVN LCID NIO BABA PYPL XYZ "
    "ROKU DKNG HOOD RBLX AFRM UPST MARA RIOT CVNA ENPH"
)

# الأسهم الكبيرة (بطاقات الموقع وتنبيهات العقود) فقط إذا سعر السهم بين 60$ و1500$
BIG_PRICE = (float(os.environ.get("BIG_MIN_PRICE", "60")), float(os.environ.get("BIG_MAX_PRICE", "1500")))


def big_price_ok(r):
    return r["sym"] == MARKET or BIG_PRICE[0] <= r["price"] <= BIG_PRICE[1]


# الإعدادات الافتراضية للنقاط العشر (الموقع يقدر يغيرها من الإعدادات)
PARAMS = dict(flow_len=10, rvol_min=2.0, spike_min=3.0, in_min=65, out_max=35, near_pct=1.0,
              near_tl=0.4, atr_mult=0.5, rs_min=1.0, mom_min=0.5, min_score=7, min_touch=3, sma_len=8)

# شروط العقد: يومي أو أقرب أسبوعي، تكلفته (سعر الطلب × 100) ضمن الميزانية، فرق ضيق، ودلتا متوسطة
OPT_RULES = dict(budget=(150, 250), max_spread=0.05, delta=(0.30, 0.50), min_liq=100,
                 max_days=7, last_0dte=(15, 0))

# الأسهم الصغيرة: السعر، وتقسيم عكسي صار قبل أسبوع إلى أسبوعين وما ارتد السهم بعده أكثر من 10%
PENNY_RULES = dict(price=(1.0, 5.0), split_age=(7, 14), rebound_max=0.10, max_symbols=60, sma_len=20)


# ---------------- المفاتيح ومصدر البيانات ----------------
def _env_file():
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.environ.get("RADAR_ENV"), "/opt/radar/.env", os.path.join(here, ".env")):
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    return dict(l.strip().split("=", 1) for l in f if "=" in l and not l.startswith("#"))
            except OSError:
                pass
    return {}


def alpaca_keys():
    key, secret = os.environ.get("ALPACA_KEY"), os.environ.get("ALPACA_SECRET")
    if not (key and secret):
        env = _env_file()
        key, secret = env.get("ALPACA_KEY"), env.get("ALPACA_SECRET")
    return (key, secret) if key and secret else None


def data_source(public=False):
    """الموقع العام يبقى على Yahoo دايماً (شرط ترخيص Alpaca)."""
    if public or os.environ.get("RADAR_PUBLIC") == "1" or os.environ.get("RADAR_DATA", "").lower() == "yahoo":
        return "yahoo"
    return "alpaca" if alpaca_keys() else "yahoo"


# ---------------- Alpaca ----------------
class Alpaca:
    DATA = "https://data.alpaca.markets"
    TRADE = "https://paper-api.alpaca.markets"

    def __init__(self, key, secret):
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
        self.cache = {}

    def get(self, url, params=None, tries=3):
        for i in range(tries):
            try:
                r = self.s.get(url, params=params, timeout=20)
                if r.status_code == 429 or r.status_code >= 500:
                    raise requests.HTTPError(f"{r.status_code}")
                r.raise_for_status()
                return r.json()
            except Exception:
                if i == tries - 1:
                    raise
                time.sleep(1.5 * (i + 1))

    def cached(self, key, ttl, fn):
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        self.cache[key] = (time.time(), val)
        return val

    def paged(self, url, params, field):
        out, params = {}, dict(params)
        while True:
            j = self.get(url, params)
            part = j.get(field) or {}
            if isinstance(part, dict):
                for k, v in part.items():
                    if isinstance(v, list):
                        out.setdefault(k, []).extend(v)
                    else:
                        out[k] = v
            else:
                out.setdefault("_list", []).extend(part)
            tok = j.get("next_page_token")
            if not tok:
                return out
            params["page_token"] = tok

    def bars(self, syms, tf, start, end=None):
        p = dict(symbols=",".join(syms), timeframe=tf, start=start.isoformat(), feed="sip",
                 adjustment="split", limit=10000)
        if end is not None:
            p["end"] = end.isoformat()
        raw = self.paged(f"{self.DATA}/v2/stocks/bars", p, "bars")
        return {s: _frame(rows, daily=tf == "1Day") for s, rows in raw.items() if rows}

    def sessions(self):
        """أيام التداول من أسبوع لقدام إلى 50 يوم ورا (للتعامل مع العطل ونهاية الأسبوع)."""
        def f():
            today = dt.datetime.now(NY).date()
            j = self.get(f"{self.TRADE}/v2/calendar",
                         dict(start=str(today - dt.timedelta(days=50)), end=str(today + dt.timedelta(days=7))))
            return [dt.date.fromisoformat(d["date"]) for d in j]
        return self.cached("sessions", 3600, f)


def _frame(rows, daily=False, rth=True):
    d = pd.DataFrame(rows)
    d.index = pd.to_datetime(d["t"], utc=True).dt.tz_convert(NY)
    d = d.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    d = d[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    if daily:
        d.index = d.index.tz_localize(None).normalize()
    elif rth:
        mins = d.index.hour * 60 + d.index.minute
        d = d[(mins >= 570) & (mins < 960)]  # وقت السوق الرسمي فقط 9:30–16:00
    d.index.name = None
    return d[~d.index.duplicated(keep="last")]


def _to_60m(b15):
    """شموع ساعة تبدأ 9:30 مثل Yahoo، مبنية من شموع 15 دقيقة."""
    if b15 is None or b15.empty:
        return None
    mins = b15.index.hour * 60 + b15.index.minute - 570
    key = b15.index.normalize() + pd.to_timedelta(570 + (mins // 60) * 60, unit="m")
    g = b15.groupby(key)
    d = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                      "Close": g["Close"].last(), "Volume": g["Volume"].sum()})
    d.index.name = None
    return d


_API = None


def api():
    global _API
    keys = alpaca_keys()
    if keys and _API is None:
        _API = Alpaca(*keys)
    return _API


def last_session(a):
    """آخر يوم تداول بدأ (اليوم إذا السوق فتح، وإلا اللي قبله)."""
    now = dt.datetime.now(NY)
    past = [d for d in a.sessions() if d < now.date() or (d == now.date() and now.hour * 60 + now.minute >= 570)]
    return past[-1] if past else now.date()


def load_alpaca(tks):
    a = api()
    tks = list(dict.fromkeys(tks))
    sess = a.sessions()
    day = last_session(a)
    prior = [d for d in sess if d < day]
    open_at = dt.datetime.combine(day, dt.time(9, 30), NY)
    start15 = dt.datetime.combine(prior[-21] if len(prior) >= 21 else prior[0], dt.time(9, 30), NY)
    key = ",".join(sorted(tks))
    daily = a.cached(("daily", key, day), 3600,
                     lambda: a.bars(tks, "1Day", open_at - dt.timedelta(days=190), open_at))
    hist15 = a.cached(("hist15", key, day), 3600, lambda: a.bars(tks, "15Min", start15, open_at))
    intr = a.bars(tks, "1Min", open_at)
    today15 = a.bars(tks, "15Min", open_at)
    b15, b60 = {}, {}
    for s in tks:
        parts = [x for x in (hist15.get(s), today15.get(s)) if x is not None and len(x)]
        if parts:
            b15[s] = pd.concat(parts)
            b60[s] = _to_60m(b15[s])
    return dict(intr=intr, daily=daily, b15=b15, b60=b60)


# ---------------- Yahoo ----------------
def _pick(df, sym):
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


def _yf(tks, period, interval):
    import yfinance as yf
    df = yf.download(list(tks), period=period, interval=interval, group_by="ticker",
                     prepost=False, threads=4, progress=False, auto_adjust=False)
    out = {}
    for s in tks:
        d = _pick(df, s)
        if d is not None:
            d = d[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            if d.index.tz is not None:
                d.index = d.index.tz_convert(NY)
            out[s] = d
    return out


def load_yahoo(tks, daily=None):
    return dict(intr=_yf(tks, "1d", "1m"), daily=daily if daily is not None else _yf(tks, "6mo", "1d"),
                b15=_yf(tks, "5d", "15m"), b60=_yf(tks, "1mo", "60m"))


def load_market(tks, source):
    tks = list(dict.fromkeys(tks))
    return load_alpaca(tks) if source == "alpaca" else load_yahoo(tks)


# ---------------- الحسابات ----------------
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


def sma_label(n):
    return f"متوسط {n} {'أيام' if n <= 10 else 'يوم'}"


def analyze(sym, intr, daily, mkt, b15, b60, p=PARAMS, side=None):
    """side=None يختار الأقوى من الصعود والهبوط، و side='up' يحسب الصعود فقط (الأسهم الصغيرة)."""
    today, past = split_days(intr, daily)
    if today is None or past is None or len(today) < 10 or len(past) < 21:
        return None
    flow_len = p["flow_len"]
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

    # المتوسط (قبل اليوم وباحتساب السعر الحالي): 8 أيام للأسهم الكبيرة و20 يوم للأسهم الصغيرة
    closes = past["Close"]
    sma_len = p.get("sma_len", PARAMS["sma_len"])
    sma_name = sma_label(sma_len)
    sma_prev = float(closes.tail(sma_len).mean())
    sma_live = float((closes.tail(sma_len - 1).sum() + price) / sma_len)
    dist = (price / sma_live - 1) * 100
    cross_up = prev_close < sma_prev and price > sma_live
    near_up = price <= sma_live and -dist <= p["near_pct"] and mom > 0
    cross_dn = prev_close > sma_prev and price < sma_live
    near_dn = price >= sma_live and dist <= p["near_pct"] and mom < 0

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
            res = res if res and res["touches"] >= p["min_touch"] else None
            sup = sup if sup and sup["touches"] >= p["min_touch"] else None
        last_c = float(cb["Close"].iloc[-1]) if cb is not None else price
        up_x = bool(res) and last_c < res["prev_val"] and price > res["val"]
        up_n = bool(res) and price <= res["val"] and (res["val"] / price - 1) * 100 <= p["near_tl"] and mom > 0
        dn_x = bool(sup) and last_c > sup["prev_val"] and price < sup["val"]
        dn_n = bool(sup) and price >= sup["val"] and (1 - sup["val"] / price) * 100 <= p["near_tl"] and mom < 0
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
        ("سيولة داخلة قوية", in_pct >= p["in_min"], f"{in_pct:.0f}%"),
        (f"اختراق {sma_name}", cross_up or near_up, ma_detail(cross_up, near_up, "اخترق")),
        ("حجم غير معتاد", rvol >= p["rvol_min"], f"{rvol:.1f}x"),
        ("فوق VWAP", price > vwap, ""),
        ("زخم صاعد", mom >= p["mom_min"], f"{mom:+.2f}%"),
        ("حركة أكبر من الطبيعي", move >= p["atr_mult"], f"{move:.1f}x"),
        ("أقوى من السوق", rs >= p["rs_min"], f"{rs:+.1f}%"),
        ("السوق داعم", mkt_up, f"SPY {mkt['chg']:+.1f}%"),
    ] + [(f"اختراق ترند هابط {n}", tl[n]["up_x"] or tl[n]["up_n"],
           trend_detail(tl[n]["res"], tl[n]["up_x"], tl[n]["up_n"], "اخترق")) for n in ("15د", "ساعة")]
    bear = [
        ("سيولة خارجة قوية", in_pct <= p["out_max"], f"{100 - in_pct:.0f}%"),
        (f"كسر {sma_name}", cross_dn or near_dn, ma_detail(cross_dn, near_dn, "كسر")),
        ("حجم غير معتاد", rvol >= p["rvol_min"], f"{rvol:.1f}x"),
        ("تحت VWAP", price < vwap, ""),
        ("زخم هابط", mom <= -p["mom_min"], f"{mom:+.2f}%"),
        ("حركة أكبر من الطبيعي", move <= -p["atr_mult"], f"{abs(move):.1f}x"),
        ("أضعف من السوق", -rs >= p["rs_min"], f"{rs:+.1f}%"),
        ("السوق ضاغط", mkt_dn, f"SPY {mkt['chg']:+.1f}%"),
    ] + [(f"كسر ترند صاعد {n}", tl[n]["dn_x"] or tl[n]["dn_n"],
           trend_detail(tl[n]["sup"], tl[n]["dn_x"], tl[n]["dn_n"], "كسر")) for n in ("15د", "ساعة")]
    s_up, s_dn = sum(ok for _, ok, _ in bull), sum(ok for _, ok, _ in bear)
    if side is None:
        side = "up" if s_up >= s_dn else "dn"
    checks, score = (bull, s_up) if side == "up" else (bear, s_dn)
    # الإشارة القوية تحتاج: النقاط الكافية + السيولة + المتوسط
    strong = score >= p["min_score"] and checks[0][1] and checks[1][1]
    accum = (rvol >= p["rvol_min"] and spike >= p["spike_min"] and abs(move) <= 0.3
             and abs(price / vwap - 1) * 100 <= 0.5)
    status = side if strong else ("acc" if accum else "")

    return dict(sym=sym, price=price, chg=chg, rvol=rvol, in_pct=in_pct,
                net_day=mf.sum(), mom=mom, sma=sma_live, sma_len=sma_len, vwap=vwap, tl=tl, side=side, score=score,
                checks=checks, status=status, unusual=rvol >= p["rvol_min"],
                last=today.index[-1])


def scan(data, tks, p=PARAMS, side=None, mkt=None):
    if mkt is None:
        mkt = market_state(data["intr"].get(MARKET), data["daily"].get(MARKET))
    out = []
    for s in tks:
        try:
            r = analyze(s, data["intr"].get(s), data["daily"].get(s), mkt,
                        data["b15"].get(s), data["b60"].get(s), p, side)
        except Exception:
            r = None
        if r:
            out.append(r)
    return out, mkt


# ---------------- مناطق السيولة في شارت السهم ----------------
# مستويات انتداول عندها حجم كبير آخر 5 جلسات: منطقة شراء إذا أغلب التدفق داخل، ومنطقة بيع إذا أغلبه خارج
ZONE_RULES = dict(days=5, bin=0.0025, span=0.05, heavy=1.5, buy=0.55, sell=0.45, touch=0.001, near=0.005, look=15)
ZONE_TEXT = {("up", "bounce"): "ارتداد من منطقة شراء", ("up", "break"): "اختراق منطقة بيع",
             ("dn", "bounce"): "رفض من منطقة بيع", ("dn", "break"): "كسر منطقة شراء"}


def liquidity_zones(bars, ref, price, rules=ZONE_RULES):
    """يرجع (مناطق الشراء، مناطق البيع)، كل منطقة (من، إلى). ref = إغلاق أمس (يثبت حدود المناطق خلال اليوم)."""
    w = ref * rules["bin"]
    flow = money_flow(bars).to_numpy(float)
    g = pd.DataFrame(dict(level=np.floor(bars["Close"].to_numpy(float) / w).astype(int), vol=bars["Volume"].to_numpy(float),
                          inflow=np.where(flow > 0, flow, 0), outflow=np.where(flow < 0, -flow, 0))).groupby("level").sum()
    g = g[(g.index * w > price * (1 - rules["span"])) & (g.index * w < price * (1 + rules["span"]))]
    if len(g) < 5:
        return [], []
    heavy = g[g["vol"] >= g["vol"].median() * rules["heavy"]]
    share = heavy["inflow"] / (heavy["inflow"] + heavy["outflow"] + 1e-9)
    return ([(k * w, (k + 1) * w) for k in heavy.index[share >= rules["buy"]]],
            [(k * w, (k + 1) * w) for k in heavy.index[share <= rules["sell"]]])


def zone_signal(today, side, buy, sell, rules=ZONE_RULES):
    """bounce = ارتداد من المنطقة، break = اختراق أو كسر مؤكد، None = ما فيه تأكيد."""
    c, n = today["Close"].to_numpy(float), rules["look"]
    if len(c) < n + 1:
        return None
    price, before = c[-1], c[-n - 1]
    low, high = float(today["Low"].tail(n).min()), float(today["High"].tail(n).max())
    if side == "up":
        if any(low <= z[1] * (1 + rules["touch"]) and z[1] < price <= z[1] * (1 + rules["near"]) for z in buy):
            return "bounce"
        return "break" if any(before < z[0] and price > z[1] for z in sell) else None
    if any(high >= z[0] * (1 - rules["touch"]) and z[0] * (1 - rules["near"]) <= price < z[0] for z in sell):
        return "bounce"
    return "break" if any(before > z[1] and price < z[0] for z in buy) else None


def stock_zone(sym, side, intr, daily, rules=ZONE_RULES):
    """تأكيد مناطق السيولة للسهم الحين. المناطق تتحدث كل 15 دقيقة."""
    a = api()
    today, past = split_days(intr, daily)
    if today is None or past is None or today.empty:
        return None
    day, now = today.index[-1].date(), today.index[-1]

    def zones():
        start = dt.datetime.combine([d for d in a.sessions() if d < day][-rules["days"]], dt.time(9, 30), NY)
        hist = a.cached(("zone_bars", sym, day), 3600, lambda: a.bars(
            [sym], "1Min", start, dt.datetime.combine(day, dt.time(9, 30), NY)).get(sym))
        bars = today if hist is None else pd.concat([hist, today])
        return liquidity_zones(bars, float(past["Close"].iloc[-1]), float(today["Close"].iloc[-1]), rules)
    buy, sell = a.cached(("zones", sym, now.floor("15min")), 900, zones)
    return zone_signal(today, side, buy, sell, rules)


# ---------------- SPY: إشارة خاصة (تجريبية) وتحذير الارتداد ----------------
# مختبرة على 80 يوم (نوفمبر 2026): زخم + حجم + اتساع السوق مع بعض، من 10:00 إلى 15:00 نيويورك
SPY_RULES = dict(mom=0.10, rvol=1.2, breadth=65, start=(10, 0), end=(15, 0))
# الارتداد أرجح عند قمة/قاع نص ساعة بعد حركة 0.20%: بعيد عن VWAP، أو أسهم قليلة ماشية معه، أو حجم مفاجئ
REV_RULES = dict(run=0.20, stretch=0.4, breadth=55, climax=2.5)


def spy_state(data, tickers, rules=SPY_RULES, rev_rules=REV_RULES):
    """حالة SPY الحين: إشارة الشروط الثلاثة (signal = up/dn/None) وتحذير الارتداد (rev)."""
    today, past = split_days(data["intr"].get(MARKET), data["daily"].get(MARKET))
    if today is None or past is None or len(today) < 12 or len(past) < 20:
        return None
    c, h, l, v = (today[k].to_numpy(float) for k in ("Close", "High", "Low", "Volume"))
    price = c[-1]
    mom = (price / c[-11] - 1) * 100
    avg_vol = float(past["Volume"].tail(20).mean())
    frac = min(max(((today.index[-1] - today.index[0]).total_seconds() / 60 + 1) / 390, 0.01), 1.0)
    rvol = v.sum() / (avg_vol * frac) if avg_vol > 0 else 0
    ups = tot = 0
    for s in tickers:
        d = data["intr"].get(s)
        if s == MARKET or d is None:
            continue
        d = d[d.index.date == today.index[-1].date()]
        if len(d) > 10:
            ups += d["Close"].iloc[-1] > d["Close"].iloc[-11]
            tot += 1
    breadth = ups / tot * 100 if tot else 50
    stretch = (price / float(vwap_of(today).iloc[-1]) - 1) * 100
    mf = money_flow(today).tail(10)
    inflow, outflow = mf[mf > 0].sum(), -mf[mf < 0].sum()
    t = today.index[-1]
    in_window = rules["start"] <= (t.hour, t.minute) <= rules["end"]
    side = "up" if mom > 0 else "dn"
    sg = 1 if side == "up" else -1
    checks = [("زخم 10 دقائق", sg * mom >= rules["mom"], f"{mom:+.2f}%"),
              ("حجم أعلى من المعتاد", rvol >= rules["rvol"], f"{rvol:.1f}x"),
              ("اتساع السوق", (breadth if side == "up" else 100 - breadth) >= rules["breadth"],
               f"{breadth if side == 'up' else 100 - breadth:.0f}%")]
    signal = side if in_window and all(ok for _, ok, _ in checks) else None

    # تحذير الارتداد: SPY عند أعلى/أدنى سعر بنص ساعة بعد حركة 0.20% أو أكثر
    rev = None
    run = (price / c[-31] - 1) * 100 if len(c) >= 36 else 0
    rdir = "up" if run > 0 else "dn"
    at_ext = h[-3:].max() >= h[-33:-3].max() if rdir == "up" else l[-3:].min() <= l[-33:-3].min()
    if abs(run) >= rev_rules["run"] and at_ext:
        rg = 1 if rdir == "up" else -1
        with_spy = breadth if rdir == "up" else 100 - breadth
        base = v[-35:-5].mean()
        climax = v[-3:].mean() / base if base > 0 else 0
        reasons = []
        if rg * stretch >= rev_rules["stretch"]:
            reasons.append(f"بعيد عن VWAP {abs(stretch):.1f}%")
        if with_spy < rev_rules["breadth"]:
            reasons.append(f"{with_spy:.0f}% بس من الأسهم ماشية معه")
        if climax >= rev_rules["climax"]:
            reasons.append(f"حجم مفاجئ {climax:.1f}x")
        if reasons:
            rev = dict(dir=rdir, reasons=reasons)
    return dict(sym=MARKET, price=price, chg=(price / float(past["Close"].iloc[-1]) - 1) * 100, side=side,
                score=sum(ok for _, ok, _ in checks), checks=checks, signal=signal, rev=rev,
                mom=mom, rvol=rvol, breadth=breadth, stretch=stretch,
                in_pct=inflow / (inflow + outflow) * 100 if inflow + outflow > 0 else 50,
                sma=float((past["Close"].tail(19).sum() + price) / 20), vwap=price / (1 + stretch / 100))


# ---------------- SPX: تنبيه خاص بعقود المؤشر ----------------
# مختبرة على 82 جلسة (نوفمبر 2026) بأسعار عقود SPX الحقيقية: 8 نقاط على شارت SPY بدون شرطي السوق.
# أمر الشراء تحت سعر التنبيه: أعلى النطاق هو سعر الأمر (مختبر)، وأسفله 5% أقل منه
SPX_RULES = dict(sym="SPX", min_score=5, start=(9, 40), main_end=(15, 0), late_end=(15, 45), cancel_by=(15, 50),
                 wait=30, min_vol=100, stale=300, span=0.025,
                 main=dict(budget=(400, 1500), order=(0.15, 0.20), stop=True),
                 late=dict(budget=(200, 350), order=(0.10, 0.15), stop=False))
SPX_DROP = {"أقوى من السوق", "أضعف من السوق", "السوق داعم", "السوق ضاغط"}


def spx_slot(now, rules=SPX_RULES):
    """الفترة الحالية: main من 9:40 إلى 15:00، و late بعدها إلى 15:45 نيويورك، وإلا None."""
    t = (now.hour, now.minute)
    if rules["start"] <= t <= rules["main_end"]:
        return "main"
    if rules["main_end"] < t <= rules["late_end"]:
        return "late"
    return None


def spx_signal(data, side, p=PARAMS, rules=SPX_RULES):
    """نقاط SPX من 8 لاتجاه واحد (الشارت شارت SPY)، أو None إذا البيانات ما تكفي."""
    intr, daily = data["intr"].get(MARKET), data["daily"].get(MARKET)
    r = analyze(MARKET, intr, daily, market_state(intr, daily), data["b15"].get(MARKET), data["b60"].get(MARKET), p, side)
    if not r:
        return None
    checks = [c for c in r["checks"] if c[0] not in SPX_DROP]
    return dict(r, sym=rules["sym"], side=side, checks=checks, score=sum(bool(ok) for _, ok, _ in checks))


def spx_level(quotes):
    """مستوى SPX من أقرب كول وبوت لبعض (Alpaca ما يعطي سعر المؤشر): السترايك + الكول - البوت."""
    best = None
    for k, q in quotes.items():
        if "call" in q and "put" in q:
            gap = abs(q["call"] - q["put"])
            if best is None or gap < best[0]:
                best = (gap, k + q["call"] - q["put"])
    return best[1] if best else None


def spx_option(kind, spy_price, slot, rules=SPX_RULES, now=None):
    """أقرب عقد SPXW للسعر ينتهي اليوم ضمن ميزانية الفترة. يرجع (العقد، السبب): ok / error / no_exp / no_match"""
    a = api()
    if a is None:
        return None, "error"
    now = now or dt.datetime.now(NY)
    today = now.date()
    ref = spy_price * 10
    try:
        contracts = a.cached(("spx", today, round(ref / 50)), 1800, lambda: a.paged(
            f"{a.TRADE}/v2/options/contracts",
            dict(underlying_symbols=rules["sym"], status="active", limit=10000, expiration_date=str(today),
                 strike_price_gte=f"{ref * (1 - rules['span']):.0f}", strike_price_lte=f"{ref * (1 + rules['span']):.0f}"),
            "option_contracts").get("_list", []))
        # SPXW تسوية آخر اليوم، وعقود SPX الشهرية تسويتها الصبح فما تناسبنا
        contracts = [c for c in contracts if c["symbol"].startswith("SPXW")]
        if not contracts:
            return None, "no_exp"
        snaps = {}
        for i in range(0, len(contracts), 100):
            snaps.update(a.paged(f"{a.DATA}/v1beta1/options/snapshots",
                                 dict(symbols=",".join(c["symbol"] for c in contracts[i:i + 100]), feed="opra", limit=1000),
                                 "snapshots"))
    except Exception:
        return None, "error"
    rows, mids = [], {}
    for c in contracts:
        s = snaps.get(c["symbol"]) or {}
        q = s.get("latestQuote") or {}
        bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if bid <= 0 or ask < bid:
            continue
        strike = float(c["strike_price"])
        mids.setdefault(strike, {})[c["type"]] = (bid + ask) / 2
        rows.append((c, s, strike, bid, ask))
    level = spx_level(mids)
    if not level:
        return None, "no_match"
    cfg = rules[slot]
    lo, hi = cfg["budget"]
    T = max((dt.datetime.combine(today, dt.time(16, 0), NY) - now).total_seconds(), 60) / (365 * 24 * 3600)
    found = []
    for c, s, strike, bid, ask in rows:
        if c["type"] != kind or not lo <= ask * 100 <= hi:
            continue
        bar, trade = s.get("dailyBar") or {}, s.get("latestTrade") or {}
        vol = int(bar.get("v") or 0) if str(bar.get("t", ""))[:10] == str(today) else 0
        try:
            age = (now - dt.datetime.fromisoformat(str(trade.get("t"))[:19] + "+00:00")).total_seconds()
        except ValueError:
            continue
        if vol < rules["min_vol"] or age > rules["stale"]:
            continue
        g = bs_greeks(kind, level, strike, T, (bid + ask) / 2)
        if g:
            found.append((g, c, strike, bid, ask, vol))
    if not found:
        return None, "no_match"
    g, c, strike, bid, ask, vol = max(found, key=lambda x: x[0]["gamma"])
    hi_pct, lo_pct = cfg["order"]
    order, low = ask * (1 - hi_pct), ask * (1 - lo_pct)
    cancel = min(now + dt.timedelta(minutes=rules["wait"]),
                 dt.datetime.combine(today, dt.time(*rules["cancel_by"]), NY))
    return dict(kind=kind, strike=strike, exp=today, today=True, exp_label=exp_label(today, today), dte=0,
                bid=bid, ask=ask, spread=(ask - bid) / ((ask + bid) / 2) * 100, cost=ask * 100, volume=vol,
                oi=int(float(c.get("open_interest") or 0)), delta=g["delta"], gamma=g["gamma"], theta=g["theta"],
                iv=g["iv"] * 100, symbol=c["symbol"], level=level, slot=slot, stop=cfg["stop"],
                order=order, low=low, cancel=cancel), "ok"


def live_quotes(syms):
    """آخر سعر ونسبة التغير من إغلاق أمس لكل سهم (طلب واحد خفيف من Alpaca)."""
    a = api()
    out = {}
    for i in range(0, len(syms), 100):
        j = a.get(f"{a.DATA}/v2/stocks/snapshots", dict(symbols=",".join(syms[i:i + 100]), feed="sip"))
        for sym, v in j.items():
            p = ((v or {}).get("latestTrade") or {}).get("p")
            prev = ((v or {}).get("prevDailyBar") or {}).get("c")
            if p and prev:
                out[sym] = (float(p), (float(p) / float(prev) - 1) * 100)
    return out


# ---------------- العقود (Alpaca / OPRA) ----------------
def exp_label(exp, today):
    if exp == today:
        return "ينتهي اليوم"
    days = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
    return f"ينتهي {days[exp.weekday()]} {exp.strftime('%m/%d')}"


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_greeks(kind, S, K, T, mid, r=0.04):
    """Alpaca ما يعطي Greeks لعقود نفس اليوم، فنحسبها من سعر العقد (Black-Scholes)."""
    if T <= 0 or mid <= 0 or S <= 0:
        return None
    call = kind == "call"

    def price(v):
        d1 = (math.log(S / K) + (r + v * v / 2) * T) / (v * math.sqrt(T))
        d2 = d1 - v * math.sqrt(T)
        if call:
            return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
        return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)

    lo, hi = 0.01, 5.0
    if not price(lo) <= mid <= price(hi):
        return None
    for _ in range(60):
        v = (lo + hi) / 2
        lo, hi = (v, hi) if price(v) < mid else (lo, v)
    v = (lo + hi) / 2
    d1 = (math.log(S / K) + (r + v * v / 2) * T) / (v * math.sqrt(T))
    d2 = d1 - v * math.sqrt(T)
    pdf = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    delta = _ncdf(d1) if call else _ncdf(d1) - 1
    gamma = pdf / (S * v * math.sqrt(T))
    carry = r * K * math.exp(-r * T) * (_ncdf(d2) if call else -_ncdf(-d2))
    theta = (-S * pdf * v / (2 * math.sqrt(T)) - carry) / 365
    return dict(delta=delta, gamma=gamma, theta=theta, iv=v)


def option_candidates(sym, kind, price, rules=OPT_RULES, now=None):
    """كل العقود المطابقة لشروطنا في أقرب انتهاء، الأفضل أولاً. يرجع (العقود، السبب): ok / error / no_exp / no_match"""
    a = api()
    if a is None:
        return [], "error"
    now = now or dt.datetime.now(NY)
    today = now.date()
    late = (now.hour, now.minute) >= rules["last_0dte"]
    try:
        contracts = a.cached(("contracts", sym, kind, today), 1800, lambda: a.paged(
            f"{a.TRADE}/v2/options/contracts",
            dict(underlying_symbols=sym, type=kind, status="active", limit=10000,
                 expiration_date_gte=str(today),
                 expiration_date_lte=str(today + dt.timedelta(days=rules["max_days"])),
                 strike_price_gte=f"{price * 0.85:.2f}", strike_price_lte=f"{price * 1.15:.2f}"),
            "option_contracts").get("_list", []))
    except Exception:
        return [], "error"
    by_exp = {}
    for c in contracts:
        e = dt.date.fromisoformat(c["expiration_date"])
        if e == today and late:
            continue  # العقد اليومي بعد الساعة 3 العصر: الثيتا قاتلة
        by_exp.setdefault(e, []).append(c)
    if not by_exp:
        return [], "no_exp"
    lo, hi = rules["budget"]
    for e in sorted(by_exp):
        cs = by_exp[e]
        try:
            snaps = {}
            for i in range(0, len(cs), 100):
                syms = ",".join(c["symbol"] for c in cs[i:i + 100])
                snaps.update(a.paged(f"{a.DATA}/v1beta1/options/snapshots",
                                     dict(symbols=syms, feed="opra", limit=1000), "snapshots"))
        except Exception:
            return [], "error"
        found = []
        for c in cs:
            s = snaps.get(c["symbol"]) or {}
            q, g = s.get("latestQuote") or {}, s.get("greeks") or {}
            bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
            if bid <= 0 or ask < bid:
                continue
            mid = (bid + ask) / 2
            if not g:
                close = dt.datetime.combine(e, dt.time(16, 0), NY)
                T = max((close - now).total_seconds(), 60) / (365 * 24 * 3600)
                g = bs_greeks(kind, price, float(c["strike_price"]), T, mid)
                if not g:
                    continue
                s = dict(s, impliedVolatility=g["iv"])
            spread = (ask - bid) / mid
            delta, gamma, theta = abs(g.get("delta", 0)), g.get("gamma", 0), g.get("theta", 0)
            bar = s.get("dailyBar") or {}
            vol = int(bar.get("v") or 0) if str(bar.get("t", ""))[:10] == str(today) else 0
            oi = int(float(c.get("open_interest") or 0))
            cost = ask * 100
            if not (lo <= cost <= hi and spread <= rules["max_spread"]
                    and rules["delta"][0] <= delta <= rules["delta"][1]
                    and max(oi, vol) >= rules["min_liq"]):
                continue
            # الأضيق فرق أولاً، بعدها الأعلى غاما، بعدها الأقل خسارة من الثيتا نسبةً لسعره
            rank = (round(spread, 2), -gamma, abs(theta) / mid)
            found.append((rank, dict(kind=kind, strike=float(c["strike_price"]), exp=e, today=e == today,
                                     exp_label=exp_label(e, today), dte=(e - today).days,
                                     bid=bid, ask=ask, spread=spread * 100, cost=cost, volume=vol, oi=oi,
                                     delta=g.get("delta", 0), gamma=gamma, theta=theta,
                                     iv=float(s.get("impliedVolatility") or 0) * 100, symbol=c["symbol"])))
        if found:
            return [o for _, o in sorted(found, key=lambda x: x[0])], "ok"
    return [], "no_match"


def best_option(sym, kind, price, rules=OPT_RULES, now=None):
    """أفضل عقد حسب شروطنا. يرجع (العقد، السبب): ok / error / no_exp / no_match"""
    found, why = option_candidates(sym, kind, price, rules, now)
    return (found[0], "ok") if found else (None, why)


# ---------------- شارت العقد نفسه ----------------
# في الكول والبوت كليهما نشتري العقد، فشارت العقد لازم يكون صاعد، والعكس في اتجاه السهم بس
CHART_RULES = dict(history_days=8, min_bars=31, div_recent=6, div_span=30, div_pivot=2, fvg_age=8, start=(9, 45), end=(15, 0))


def contract_bars(symbols, now=None, rules=CHART_RULES):
    """شموع 15 دقيقة المكتملة لكل عقد: {الرمز: DataFrame}"""
    a = api()
    now = now or dt.datetime.now(NY)
    start = dt.datetime.combine(now.date() - dt.timedelta(days=rules["history_days"]), dt.time(9, 30), NY)
    out = {}
    for i in range(0, len(symbols), 100):
        raw = a.paged(f"{a.DATA}/v1beta1/options/bars",
                      dict(symbols=",".join(symbols[i:i + 100]), timeframe="15Min", limit=10000,
                           start=start.isoformat(), end=now.isoformat()), "bars")
        for sym, rows in raw.items():
            cb = completed(_frame(rows, rth=False), 15, pd.Timestamp(now)) if rows else None
            if cb is not None:
                out[sym] = cb
    return out


def rsi_of(close, n=14):
    d = np.diff(close, prepend=close[0])
    up = pd.Series(np.where(d > 0, d, 0.0)).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    dn = pd.Series(np.where(d < 0, -d, 0.0)).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    return 100 - 100 / (1 + up / np.maximum(dn, 1e-9))


def bull_divergence(cb, rules=CHART_RULES):
    """دايفيرجنس إيجابي: قاع أدنى في السعر مع قاع أعلى في RSI، وآخر قاع قريب."""
    d = cb.tail(80)
    r, low, n, k = rsi_of(d["Close"].to_numpy(float)), d["Low"].to_numpy(float), len(d), rules["div_pivot"]
    piv = [i for i in range(max(k, n - rules["div_span"] - rules["div_recent"]), n - k)
           if low[i] == low[i - k:i + k + 1].min()]
    if len(piv) < 2 or n - 1 - piv[-1] > rules["div_recent"]:
        return False
    for p in reversed(piv[:-1]):
        if piv[-1] - p >= 4:
            return bool(low[piv[-1]] < low[p] and r[piv[-1]] > r[p])
    return False


def bull_fvg(cb, price, rules=CHART_RULES):
    """فير فاليو قاب صاعد: فجوة بين ثلاث شموع لسا ما انسدت، والسعر فوقها."""
    d = cb.tail(rules["fvg_age"] + 3)
    h, l = d["High"].to_numpy(float), d["Low"].to_numpy(float)
    return any(h[i - 2] < l[i] and l[i:].min() > h[i - 2] and price > h[i - 2] for i in range(len(d) - 1, 1, -1))


def trend_broke_today(cb, p=PARAMS):
    """هل سكرت شمعة 15 دقيقة اليوم فوق ترند هابط صالح؟"""
    day = cb.index[-1].date()
    for i in range(len(cb) - 1, 0, -1):
        if cb.index[i].date() != day:
            return False
        res = find_trendline(cb.iloc[:i], "res")
        if (res and res["touches"] >= p["min_touch"] and float(cb["Close"].iloc[i]) > res["val"]
                and float(cb["Close"].iloc[i - 1]) <= res["prev_val"]):
            return True
    return False


def contract_checks(cb, price):
    """الشروط الثلاثة الإجبارية على شارت العقد."""
    return [("كسر ترند هابط 15د", trend_broke_today(cb), ""),
            ("دايفيرجنس إيجابي", bull_divergence(cb), ""),
            ("فير فاليو قاب صاعد", bull_fvg(cb, price), "")]


def stock_side(intr):
    """اتجاه السهم الحين: up فوق VWAP وزخم 10 دقائق موجب، dn العكس، وإلا None."""
    if intr is None or len(intr) < 11:
        return None
    today = intr[intr.index.date == intr.index[-1].date()]
    if len(today) < 11:
        return None
    price, vwap = float(today["Close"].iloc[-1]), float(vwap_of(today).iloc[-1])
    mom = price / float(today["Close"].iloc[-11]) - 1
    return "up" if price > vwap and mom > 0 else "dn" if price < vwap and mom < 0 else None


def contract_scan(data, tickers, now=None, rules=CHART_RULES):
    """لكل سهم اتجاهه واضح: العقود المطابقة، ثم شارت كل عقد. يرجع [(السهم، الاتجاه، السعر، العقد، الشروط، الشروط الثلاثة متحققة)]."""
    now = now or dt.datetime.now(NY)
    picks = []
    for s in tickers:
        intr = data["intr"].get(s)
        side = stock_side(intr)
        if s == MARKET or not side:
            continue
        price = float(intr["Close"].iloc[-1])
        if not BIG_PRICE[0] <= price <= BIG_PRICE[1]:
            continue
        cands, _ = option_candidates(s, "call" if side == "up" else "put", price, now=now)
        picks += [(s, side, price, o) for o in cands]
    bars = contract_bars([o["symbol"] for *_, o in picks], now, rules) if picks else {}
    out = []
    for s, side, price, o in picks:
        cb = bars.get(o["symbol"])
        if cb is None or len(cb) < rules["min_bars"]:
            continue
        checks = contract_checks(cb, (o["bid"] + o["ask"]) / 2)
        out.append(dict(sym=s, side=side, price=price, opt=o, checks=checks, ok=all(c[1] for c in checks)))
    return out


# ---------------- الأسهم الصغيرة ----------------
def penny_universe(rules=PENNY_RULES):
    """الأسهم الأكثر تداولاً والأكثر صعوداً اليوم اللي سعرها بين 1 و5 دولار."""
    a = api()

    def names():
        rows = a.get(f"{a.TRADE}/v2/assets", dict(status="active", asset_class="us_equity"))
        return {x["symbol"]: x.get("name") or "" for x in rows if x.get("tradable")}

    def f():
        syms = []
        for by in ("volume", "trades"):
            act = a.get(f"{a.DATA}/v1beta1/screener/stocks/most-actives", dict(by=by, top=100))
            syms += [x["symbol"] for x in act.get("most_actives", [])]
        mov = a.get(f"{a.DATA}/v1beta1/screener/stocks/movers", dict(top=50))
        syms += [x["symbol"] for x in mov.get("gainers", []) + mov.get("losers", [])]
        # ونضيف كل الأسهم اللي سوت تقسيم عكسي خلال آخر أسبوعين
        today = dt.datetime.now(NY).date()
        rs = a.paged(f"{a.DATA}/v1/corporate-actions",
                     dict(types="reverse_split", limit=1000, end=str(today),
                          start=str(today - dt.timedelta(days=rules["split_age"][1]))), "corporate_actions")
        syms += [x["symbol"] for x in rs.get("reverse_splits", [])]
        # أسهم شركات فقط: نستبعد صناديق ETF والوارنتات والوحدات والحقوق
        listed = a.cached("asset_names", 86400, names)
        fund = ("ETF", "ETN", " Fund", " Trust")
        syms = [s for s in dict.fromkeys(syms) if s in listed and "." not in s
                and not (len(s) == 5 and s[-1] in "WUR") and not any(w in listed[s] for w in fund)]
        snaps = {}
        for i in range(0, len(syms), 100):
            snaps.update(a.get(f"{a.DATA}/v2/stocks/snapshots",
                               dict(symbols=",".join(syms[i:i + 100]), feed="sip")))
        lo, hi = rules["price"]
        rows = []
        for s, sn in snaps.items():
            px = float(((sn or {}).get("latestTrade") or {}).get("p") or 0)
            vol = float(((sn or {}).get("dailyBar") or {}).get("v") or 0)
            if lo <= px <= hi:
                rows.append((vol, s))
        return [s for _, s in sorted(rows, reverse=True)[:rules["max_symbols"]]]
    return a.cached("penny_universe", 300, f)


def reverse_splits(syms, rules=PENNY_RULES):
    """التقسيمات العكسية خلال الفترة المحددة: {الرمز: (تاريخ التنفيذ، النسبة)}"""
    a = api()
    if not syms:
        return {}
    today = dt.datetime.now(NY).date()

    def f():
        j = a.paged(f"{a.DATA}/v1/corporate-actions",
                    dict(symbols=",".join(syms), types="reverse_split", limit=1000,
                         start=str(today - dt.timedelta(days=rules["split_age"][1])), end=str(today)),
                    "corporate_actions")
        out = {}
        for x in j.get("reverse_splits", []):
            out[x["symbol"]] = (dt.date.fromisoformat(x["ex_date"]),
                                f"1:{round(x['old_rate'] / x['new_rate'], 2):g}")
        return out
    return a.cached(("splits", ",".join(sorted(syms)), today), 1800, f)


def split_info(sym, split, daily, intr, rules=PENNY_RULES):
    """معلومة التقسيم تطلع بس إذا: صار قبل أسبوع إلى أسبوعين، والسهم نزل بعده، وما ارتد أكثر من 10%."""
    if not split or daily is None or intr is None or intr.empty:
        return None
    ex, ratio = split
    age = (intr.index[-1].date() - ex).days
    if not rules["split_age"][0] <= age <= rules["split_age"][1]:
        return None
    after = daily[daily.index.date >= ex]
    if after.empty:
        return None
    first_open = float(after["Open"].iloc[0])
    top = max(float(after["High"].max()), float(intr["High"].max()))
    price = float(intr["Close"].iloc[-1])
    if price >= first_open or top > first_open * (1 + rules["rebound_max"]):
        return None
    return dict(ratio=ratio, date=ex.strftime("%m/%d"), drop=(1 - price / first_open) * 100)


def penny_extra(sym, daily, intr):
    """معلومات إضافية لبطاقة السهم الصغير: البعد عن أعلى سعر اليوم، وحجم ما قبل الافتتاح ÷ يوم عادي، وعناوين أخباره اليوم."""
    a = api()
    today, past = split_days(intr, daily)
    out = dict(off_high=0.0, pm=None, news=[])
    if today is None or today.empty:
        return out
    out["off_high"] = (float(today["Close"].iloc[-1]) / float(today["High"].max()) - 1) * 100
    day = today.index[-1].date()
    try:
        raw = a.paged(f"{a.DATA}/v2/stocks/bars", dict(symbols=sym, timeframe="1Min", feed="sip", adjustment="split",
                      start=dt.datetime.combine(day, dt.time(4), NY).isoformat(),
                      end=dt.datetime.combine(day, dt.time(9, 30), NY).isoformat(), limit=10000), "bars")
        avg = float(past["Volume"].tail(20).mean()) if past is not None and len(past) else 0
        if avg > 0:
            out["pm"] = sum(b["v"] for b in raw.get(sym, [])) / avg
    except Exception:
        pass
    try:
        j = a.get(f"{a.DATA}/v1beta1/news", dict(symbols=sym, limit=5,
                  start=(dt.datetime.combine(day, dt.time(4), NY) - dt.timedelta(hours=12)).isoformat()))
        # أخبار السهم نفسه، مو أخبار السوق العامة اللي تذكر عشرات الأسهم
        out["news"] = [n["headline"] for n in j.get("news", []) if len(n.get("symbols", [])) <= 3]
    except Exception:
        pass
    return out


def penny_scan(p=PARAMS, mkt=None, rules=PENNY_RULES):
    syms = penny_universe(rules)
    if not syms:
        return [], {}
    data = load_alpaca(syms + [MARKET])
    res, _ = scan(data, syms, dict(p, sma_len=rules["sma_len"]), side="up", mkt=mkt)
    lo, hi = rules["price"]
    res = [r for r in res if lo <= r["price"] <= hi]
    splits = reverse_splits([r["sym"] for r in res], rules)
    for r in res:
        r["split"] = split_info(r["sym"], splits.get(r["sym"]), data["daily"].get(r["sym"]),
                                data["intr"].get(r["sym"]), rules)
    return res, data
