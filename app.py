"""
رادار السيولة — زخم الأسهم الأمريكية، السيولة الداخلة والخارجة، ونقاط الإشارة
تشغيل محلي:  pip install -r requirements.txt  ثم  streamlit run app.py
"""
import os
import json
import time
import streamlit.components.v1 as components
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import base64
import io
from PIL import Image

import radar_core as rc
from radar_core import money_flow, vwap_of, split_days

# لوقو الموقع (الدب والثور، يظهر دائري) من assets/logo.webp
LOGO_B64 = base64.b64encode(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.webp"), "rb").read()).decode()
LOGO_IMG = Image.open(io.BytesIO(base64.b64decode(LOGO_B64)))

st.set_page_config(page_title="رادار السيولة", page_icon=LOGO_IMG, layout="wide")

# نسخة الأهل: بدون أوبشن، ومع أسعار TradingView اللحظية
PUBLIC = os.environ.get("RADAR_PUBLIC") == "1"
TV_COPY = ("<div class='tradingview-widget-copyright' style='font:11px Arial;color:#8C97A8;text-align:center'>"
           "<a href='https://www.tradingview.com/' rel='noopener nofollow' target='_blank' style='color:#8C97A8'>"
           "Charts and quotes by TradingView</a></div>")


# المؤشرات الرسمية (SPX وNDX وVIX) ممنوعة في ودجت TradingView المجاني، فنعرض عقود CFD اللي تتبعها لحظياً
INDICES = (("FOREXCOM:SPXUSD", "S&P 500"), ("FOREXCOM:NSXUSD", "NASDAQ 100"), ("CAPITALCOM:VIX", "VIX"))


def tv_indices():
    # كل مؤشر في عمود لحاله عشان الثلاثة يبانون حتى في الجوال (الأعمدة تنرص فوق بعض)
    for col, (sym, title) in zip(st.columns(len(INDICES)), INDICES):
        cfg = {"symbol": sym, "width": "100%", "isTransparent": True, "colorTheme": "dark", "locale": "en"}
        with col:
            components.html(
                "<div class='tradingview-widget-container'><div class='tradingview-widget-container__widget'></div>"
                "<script type='text/javascript' src='https://s3.tradingview.com/external-embedding/embed-widget-single-quote.js' async>"
                + json.dumps(cfg) + "</script></div>", height=90)


def tv_chart(symbol, height=520):
    cfg = {"autosize": True, "symbol": symbol, "interval": "1", "timezone": "Asia/Riyadh", "theme": "dark",
           "style": "1", "locale": "en", "allow_symbol_change": False, "hide_side_toolbar": False,
           "backgroundColor": "#000000", "support_host": "https://www.tradingview.com"}
    components.html(
        f"<div class='tradingview-widget-container' style='height:{height - 30}px;width:100%'>"
        f"<div class='tradingview-widget-container__widget' style='height:100%;width:100%'></div>"
        "<script type='text/javascript' src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js' async>"
        + json.dumps(cfg) + "</script></div>" + TV_COPY, height=height)

DEFAULT_TICKERS, MARKET = rc.DEFAULT_TICKERS, rc.MARKET
# الموقع الخاص على Alpaca (لحظي) إذا المفاتيح موجودة، والموقع العام على Yahoo دايماً
SOURCE = rc.data_source(PUBLIC)
LIVE = SOURCE == "alpaca"
IN_C, OUT_C, SIG_C, MUTED = "#3CC7A6", "#EE6B5E", "#F2B544", "#8C97A8"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;700&display=swap');
html, body, .stApp, .stMarkdown, button, input, textarea, select {{
  font-family: 'IBM Plex Sans Arabic', system-ui, sans-serif !important; }}
.rtl {{ direction: rtl; text-align: right; }}
h1.title {{ direction: rtl; text-align: right; font-size: 1.9rem; font-weight: 700; margin: 0; padding: 0; }}
.brand {{ direction: rtl; display: flex; align-items: center; gap: .8rem; margin-bottom: .3rem; }}
.brand img {{ width: 130px; height: 130px; border-radius: 50%; flex-shrink: 0;
              box-shadow: 0 0 0 1px #26272E, 0 6px 24px rgba(0,0,0,.45); }}
.brand {{ gap: 1.1rem; margin-bottom: .6rem; }}
@media (max-width: 640px) {{ .brand img {{ width: 96px; height: 96px; }} h1.title {{ font-size: 1.6rem; }} }}
.sub {{ direction: rtl; text-align: right; color: {MUTED}; font-size: .95rem; margin-bottom: 1rem; }}
.row {{ direction: rtl; text-align: right; background: #141519; border: 1px solid #26272E;
        border-radius: 10px; padding: .8rem 1rem; margin-bottom: .55rem; }}
.row.up {{ border-color: {IN_C}; }} .row.dn {{ border-color: {OUT_C}; }} .row.acc {{ border-color: {SIG_C}; }}
.head {{ display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: .5rem; }}
.h-r {{ display: flex; align-items: center; gap: .55rem; flex-wrap: wrap; }}
.h-c {{ justify-self: center; }}
.h-l {{ justify-self: end; }}
.opt {{ display: flex; align-items: center; gap: .55rem; padding: .25rem .75rem; border-radius: 8px;
        border: 1px solid #26272E; background: #1C1D22; font-size: .82rem; white-space: nowrap; }}
.opt .k {{ font-weight: 700; letter-spacing: .03em; direction: ltr; }}
.opt.call .k {{ color: {IN_C}; }} .opt.put .k {{ color: {OUT_C}; }}
.opt .n {{ font-weight: 700; direction: ltr; unicode-bidi: isolate; }}
.opt .x {{ color: {MUTED}; direction: ltr; unicode-bidi: isolate; }}
.opt.none {{ color: {MUTED}; }}
.opt .e {{ color: {MUTED}; }} .opt .e.today {{ color: {SIG_C}; font-weight: 700; }}
@media (max-width: 640px) {{
  .head {{ grid-template-columns: 1fr auto; }}
  .h-c {{ grid-column: 1 / -1; grid-row: 2; justify-self: stretch; }}
  .opt {{ justify-content: center; }} }}
.sym {{ font-size: 1.25rem; font-weight: 700; letter-spacing: .02em; }}
.badge {{ font-size: .78rem; padding: .1rem .55rem; border-radius: 99px; font-weight: 500; }}
.b-up {{ background: {IN_C}; color: #000000; }}
.b-dn {{ background: {OUT_C}; color: #000000; }}
.b-acc {{ background: rgba(242,181,68,.18); color: {SIG_C}; }}
.b-warn {{ background: rgba(238,107,94,.16); color: {OUT_C}; }}
.price {{ font-size: 1.05rem; font-weight: 500; direction: ltr; unicode-bidi: isolate; }}
.pct {{ font-size: .82rem; font-weight: 700; padding: .12rem .5rem; border-radius: 6px;
        direction: ltr; unicode-bidi: isolate; }}
.pct.up {{ background: rgba(60,199,166,.15); }} .pct.down {{ background: rgba(238,107,94,.15); }}
.up {{ color: {IN_C}; }} .down {{ color: {OUT_C}; }}
.bar {{ display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin: .6rem 0 .5rem; background: {OUT_C}; }}
.bar .in {{ background: {IN_C}; }}
.stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: .4rem; }}
.stat {{ text-align: right; }}
.stat .k {{ color: {MUTED}; font-size: .75rem; text-align: right; }}
.stat .v {{ font-size: .95rem; font-weight: 500; direction: ltr; text-align: right; }}
.score {{ display: flex; align-items: center; gap: .6rem; margin: .7rem 0 .4rem; }}
.score .t {{ font-size: .85rem; font-weight: 500; white-space: nowrap; }}
.meter {{ flex: 1; height: 6px; border-radius: 3px; background: #26272E; overflow: hidden; }}
.meter div {{ height: 100%; }}
.checks {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: .3rem .6rem; }}
.chk {{ font-size: .78rem; color: {MUTED}; }}
.chk.ok {{ color: #E6EAF0; }}
.chk .m {{ display: inline-block; width: 1.1em; font-weight: 700; }}
.chk .d {{ color: {MUTED}; direction: ltr; unicode-bidi: isolate; }}
@media (max-width: 640px) {{
  .stats, .checks {{ grid-template-columns: repeat(2, 1fr); }} }}
.price.fu {{ animation: fu 1.2s ease-out; }} .price.fd {{ animation: fd 1.2s ease-out; }}
@keyframes fu {{ 0% {{ color: {IN_C}; background: rgba(60,199,166,.25); }} 100% {{ background: transparent; }} }}
@keyframes fd {{ 0% {{ color: {OUT_C}; background: rgba(238,107,94,.25); }} 100% {{ background: transparent; }} }}
.price {{ border-radius: 5px; padding: 0 .2rem; }}
.live {{ display: inline-block; width: .55rem; height: .55rem; border-radius: 50%; background: {IN_C};
         margin-left: .35rem; animation: pulse 2s infinite; vertical-align: middle; }}
@keyframes pulse {{ 0% {{ box-shadow: 0 0 0 0 rgba(60,199,166,.6); }} 70% {{ box-shadow: 0 0 0 .5rem rgba(60,199,166,0); }}
                   100% {{ box-shadow: 0 0 0 0 rgba(60,199,166,0); }} }}
/* تحديث صامت: لا تعتيم ولا مؤشر تشغيل أثناء التحديث */
[data-stale="true"], .stale-element {{ opacity: 1 !important; filter: none !important; transition: none !important; }}
[data-testid="stStatusWidget"] {{ visibility: hidden !important; }}
</style>
""", unsafe_allow_html=True)

# ---------------- الإعدادات ----------------
with st.sidebar:
    st.markdown("<div class='rtl'><b>الإعدادات</b></div>", unsafe_allow_html=True)
    tickers_txt = st.text_area("الأسهم (افصل بمسافة)", DEFAULT_TICKERS, height=150)
    min_score = st.slider("أقل عدد نقاط للإشارة القوية (من 10)", 4, 10, 7)
    flow_len = st.slider("فترة حساب السيولة (دقائق)", 5, 60, 10)
    rvol_min = st.slider("أقل حجم مقابل المعتاد (RVOL)", 1.0, 5.0, 2.0, 0.1)
    in_min = st.slider("سيولة داخلة قوية من %", 50, 90, 65)
    out_max = st.slider("سيولة خارجة قوية تحت %", 10, 50, 35)
    near_pct = st.slider("قرب متوسط 20 يوم (%)", 0.2, 3.0, 1.0, 0.1)
    atr_mult = st.slider("أقل حركة مقابل الحركة الطبيعية (ATR)", 0.2, 1.5, 0.5, 0.1)
    rs_min = st.slider("أقوى/أضعف من السوق بـ %", 0.0, 3.0, 1.0, 0.1)
    min_touch = st.slider("أقل عدد لمسات لخط الترند", 2, 4, 3)
    near_tl = st.slider("قرب خط الترند (%)", 0.1, 1.5, 0.4, 0.1)
    mom_min = st.slider("أقل زخم % خلال فترة السيولة", 0.0, 3.0, 0.5, 0.1)
    spike_min = st.slider("قفزة حجم آخر 5 دقائق للتجميع (x)", 1.0, 8.0, 3.0, 0.5)
    p = dict(flow_len=flow_len, rvol_min=rvol_min, spike_min=spike_min, in_min=in_min, out_max=out_max,
             near_pct=near_pct, near_tl=near_tl, atr_mult=atr_mult, rs_min=rs_min, mom_min=mom_min,
             min_score=min_score, min_touch=min_touch)
    opt_rules, opt_dte, opt_min_oi = None, (5, 21), 100
    if PUBLIC:
        show_opt, opt_budget = False, (100, 400)
    else:
        show_opt = st.toggle("عرض أفضل عقد أوبشن", value=True)
        opt_budget = st.slider("سعر العقد ($)", 50, 800, (100, 400), 10)
        if LIVE:
            opt_spread = st.slider("أقصى فرق بين العرض والطلب (%)", 1, 15, 5)
            opt_delta = st.slider("الدلتا", 0.10, 0.90, (0.30, 0.50), 0.05)
            opt_rules = dict(rc.OPT_RULES, budget=opt_budget, max_spread=opt_spread / 100, delta=opt_delta)
        else:
            opt_dte = st.slider("أيام لين انتهاء العقد", 1, 60, (5, 21))
            opt_min_oi = st.slider("أقل عقود مفتوحة (Open Interest)", 0, 2000, 100, 50)
    auto = st.toggle("تحديث تلقائي", value=True)

tickers = tuple(dict.fromkeys(t.strip().upper() for t in tickers_txt.split() if t.strip()))
fetch = tuple(dict.fromkeys(tickers + (MARKET,)))


# ---------------- البيانات ----------------
REFRESH = 20 if LIVE else 60


TICK = 3 if LIVE else REFRESH  # ثواني بين كل تحديث للأسعار في الموقع الخاص


# التحليل الثقيل مشترك بين الزوار وما ينسخ كل مرة (cache_resource)، والأسعار الخفيفة تتحدث كل 3 ثواني
@st.cache_resource(ttl=REFRESH - 5 if LIVE else REFRESH - 2, show_spinner=False)
def load_big(tks, fetch, source, params):
    data = rc.load_market(fetch, source)
    results, mkt = rc.scan(data, tks, dict(params))
    return [r for r in results if rc.big_price_ok(r)], mkt, data


@st.cache_resource(ttl=REFRESH - 5 if LIVE else REFRESH - 2, show_spinner=False)
def load_penny(params):
    results, data = rc.penny_scan(dict(params))
    mkt = rc.market_state(data["intr"].get(MARKET), data["daily"].get(MARKET)) if data else None
    return results, mkt, data


@st.cache_data(ttl=2, show_spinner=False)
def live_quotes(syms):
    try:
        return rc.live_quotes(list(syms))
    except Exception:
        return {}


def market_hours():
    now = pd.Timestamp.now(tz="America/New_York")
    return now.weekday() < 5 and 570 <= now.hour * 60 + now.minute < 960


@st.cache_resource
def _opt_store():
    return {}


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


def opt_html(res, kind):
    if not show_opt or res is None:
        return ""
    o, why = res
    name = "كول" if kind == "call" else "بوت"
    if o is None:
        msg = {"error": "تعذر جلب الأوبشن، جرّب بعد دقيقة",
               "no_exp": "ما فيه انتهاء ضمن المدة المحددة",
               "no_match": f"ما فيه {name} مطابق للشروط" if LIVE else
                           f"ما فيه {name} بين ${opt_budget[0]} و${opt_budget[1]}"}[why]
        return f"<div class='opt none'>{msg}</div>"
    strike = f"{o['strike']:g}"
    if LIVE:
        tip = (f"العرض ${o['bid']:.2f} | الطلب ${o['ask']:.2f} | دلتا {o['delta']:.2f} | "
               f"غاما {o['gamma']:.3f} | ثيتا {o['theta']:.2f} | Open Interest {o['oi']:,}")
        return (f"<div class='opt {o['kind']}' title='{tip}'>"
                f"<span class='k'>{o['kind'].upper()}</span><span class='n'>${strike}</span>"
                f"<span class='e{' today' if o['today'] else ''}'>{o['exp_label']}</span>"
                f"<span class='n'>${o['cost']:.0f}</span><span class='x'>فرق {o['spread']:.1f}%</span></div>")
    age = ""
    if o.get("last") is not None and not pd.isna(o["last"]):
        mins = (pd.Timestamp.now(tz="UTC") - o["last"]).total_seconds() / 60
        if mins < 60:
            age = f"<span class='x'>قبل {max(mins, 0):.0f}د</span>"
        else:
            age = f"<span class='x'>آخر صفقة {o['last'].tz_convert('America/New_York').strftime('%m/%d %H:%M')}</span>"
    oi = f"Open Interest {o['oi']:,}" if o["oi"] else "Open Interest غير متوفر"
    return (f"<div class='opt {o['kind']}' title='{oi} | IV {o['iv']:.0f}%'>"
            f"<span class='k'>{o['kind'].upper()}</span><span class='n'>${strike}</span>"
            f"<span class='x'>{o['exp'].strftime('%m/%d')}</span><span class='x'>{o['dte']} يوم</span>"
            f"<span class='n'>${o['cost']:.0f}</span>{age}</div>")


def money(x):
    s = "+" if x >= 0 else "−"
    x = abs(x)
    for div, u in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= div:
            return f"{s}${x / div:.1f}{u}"
    return f"{s}${x:.0f}"


BADGES = {"up": ("b-up", "إشارة صعود قوية"), "dn": ("b-dn", "إشارة هبوط قوية"),
          "acc": ("b-acc", "تجميع محتمل")}


def row_html(r):
    badge = ""
    if r["status"]:
        cls, txt = BADGES[r["status"]]
        badge = f"<span class='badge {cls}'>{txt}</span>"
    if r.get("split"):
        sp = r["split"]
        badge = (f"<span class='badge b-acc'>تقسيم عكسي {sp['ratio']} · {sp['date']} · "
                 f"نزل بعده {sp['drop']:.1f}%</span> ") + badge
    badge = r.get("spy_badge", "") + badge
    cc = lambda x: "up" if x >= 0 else "down"
    side_c = IN_C if r["side"] == "up" else OUT_C
    side_t = "صعود" if r["side"] == "up" else "هبوط"
    chips = "".join(
        f"<div class='chk {'ok' if ok else ''}'><span class='m' style='color:{side_c if ok else MUTED}'>"
        f"{'✓' if ok else '·'}</span>{name} <span class='d'>{d}</span></div>"
        for name, ok, d in r["checks"])
    return f"""
<div class='row {r["status"]}'>
  <div class='head'>
    <div class='h-r'>
      <span class='sym'>{r["sym"]}</span>
      <span class='price{r.get("flash", "")}'>${r["price"]:.2f}</span>
      <span class='pct {cc(r["chg"])}'>{r["chg"]:+.2f}%</span>
    </div>
    <div class='h-c'>{opt_html(r.get("opt"), "call" if r["side"] == "up" else "put")}</div>
    <div class='h-l'>{badge}</div>
  </div>
  <div class='bar'><div class='in' style='width:{r["in_pct"]:.0f}%'></div></div>
  <div class='stats'>
    <div class='stat'><div class='k'>السيولة الداخلة ({flow_len}د)</div><div class='v'>{r["in_pct"]:.0f}%</div></div>
    <div class='stat'><div class='k'>صافي السيولة اليوم</div><div class='v {cc(r["net_day"])}'>{money(r["net_day"])}</div></div>
    <div class='stat'><div class='k'>الزخم ({flow_len}د)</div><div class='v {cc(r["mom"])}'>{r["mom"]:+.2f}%</div></div>
    <div class='stat'><div class='k'>الحجم مقابل المعتاد</div><div class='v'>{r["rvol"]:.1f}x</div></div>
  </div>
  <div class='score'>
    <span class='t' style='color:{side_c}'>نقاط ال{side_t}: {r["score"]}/10</span>
    <div class='meter'><div style='width:{r["score"] / 10 * 100:.0f}%;background:{side_c}'></div></div>
  </div>
  <div class='checks'>{chips}</div>
</div>"""


def detail_chart(intr, sma):
    d, _ = split_days(intr, intr)
    mf = money_flow(d)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.5, 0.25, 0.25],
                        vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=d.index, y=d["Close"], name="السعر", line=dict(color="#E6EAF0", width=1.6)), 1, 1)
    fig.add_trace(go.Scatter(x=d.index, y=vwap_of(d), name="VWAP", line=dict(color=SIG_C, width=1, dash="dot")), 1, 1)
    fig.add_trace(go.Scatter(x=[d.index[0], d.index[-1]], y=[sma, sma], name="متوسط 20 يوم",
                             line=dict(color="#7FA7E8", width=1.2, dash="dash")), 1, 1)
    fig.add_trace(go.Bar(x=d.index, y=mf, name="السيولة كل دقيقة",
                         marker_color=np.where(mf >= 0, IN_C, OUT_C)), 2, 1)
    cum = mf.cumsum()
    fig.add_trace(go.Scatter(x=d.index, y=cum, name="صافي السيولة التراكمي", fill="tozeroy",
                             line=dict(color=IN_C if cum.iloc[-1] >= 0 else OUT_C, width=1.4)), 3, 1)
    fig.update_layout(height=560, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
                      paper_bgcolor="#000000", plot_bgcolor="#000000", font=dict(color="#E6EAF0"),
                      bargap=0)
    fig.update_xaxes(gridcolor="#26272E")
    fig.update_yaxes(gridcolor="#26272E")
    return fig


def daily_chart(daily, intr):
    today, past = split_days(intr, daily)
    d = past.tail(120)[["Open", "High", "Low", "Close"]].copy()
    ts = pd.Timestamp(today.index[-1].date())
    if d.index.tz is not None:
        ts = ts.tz_localize(d.index.tz)
    d.loc[ts] = [today["Open"].iloc[0], today["High"].max(),
                                                   today["Low"].min(), today["Close"].iloc[-1]]
    fig = go.Figure(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
                                   increasing_line_color=IN_C, decreasing_line_color=OUT_C, name="السعر"))
    sma = d["Close"].rolling(20).mean()
    fig.add_trace(go.Scatter(x=d.index, y=sma, name="متوسط 20 يوم", line=dict(color="#7FA7E8", width=1.3)))
    fig.update_layout(height=460, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
                      paper_bgcolor="#000000", plot_bgcolor="#000000", font=dict(color="#E6EAF0"),
                      xaxis_rangeslider_visible=False)
    fig.update_xaxes(gridcolor="#26272E", rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_yaxes(gridcolor="#26272E")
    return fig


def tf_chart(bars, line_info):
    """شارت 15 دقيقة أو ساعة مع خطوط الترند المكتشفة."""
    d = bars.tail(130).copy()
    x = d.index.tz_convert("America/New_York").tz_localize(None) if d.index.tz is not None else d.index
    fig = go.Figure(go.Candlestick(x=x, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"],
                                   increasing_line_color=IN_C, decreasing_line_color=OUT_C, name="السعر"))
    for key, color, label in (("res", OUT_C, "ترند هابط"), ("sup", IN_C, "ترند صاعد")):
        t = line_info[key]
        if t:
            mask = d.index >= t["start"]
            xs = x[mask][: t["n"] - t["i1"] + 1]
            ys = t["y1"] + t["slope"] * np.arange(len(xs))
            fig.add_trace(go.Scatter(x=xs, y=ys, name=label, mode="lines",
                                     line=dict(color=color, width=2, dash="dash")))
    fig.update_layout(height=440, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
                      paper_bgcolor="#000000", plot_bgcolor="#000000", font=dict(color="#E6EAF0"),
                      xaxis_rangeslider_visible=False)
    fig.update_xaxes(gridcolor="#26272E",
                     rangebreaks=[dict(bounds=["sat", "mon"]), dict(bounds=[16, 9.5], pattern="hour")])
    fig.update_yaxes(gridcolor="#26272E")
    return fig


# ---------------- الواجهة ----------------
st.markdown(f"<div class='brand'><img src='data:image/webp;base64,{LOGO_B64}' alt=''>"
            f"<h1 class='title'>رادار السيولة</h1></div>", unsafe_allow_html=True)


def get_results(penny):
    """النتائج من الكاش المشترك؛ أول مرة يطلع مؤشر تحميل، بعدها بصمت."""
    note = None
    if not st.session_state.get(f"loaded_{penny}"):
        note = st.empty()
        note.markdown("<div class='sub'>جاري تحميل البيانات…</div>", unsafe_allow_html=True)
    try:
        out = load_penny(tuple(sorted(p.items()))) if penny else load_big(tickers, fetch, SOURCE, tuple(sorted(p.items())))
    except Exception:
        out = None
    if note:
        note.empty()
    if out:
        st.session_state[f"loaded_{penny}"] = True
    return out


def spy_badge(data):
    """على بطاقة SPY: إشارته التجريبية (زخم + حجم + اتساع) وتحذير الارتداد."""
    try:
        s = rc.spy_state(data, list(tickers))
    except Exception:
        return ""
    if not s:
        return ""
    out = ""
    if s["signal"]:
        word = "صعود" if s["signal"] == "up" else "هبوط"
        out += f"<span class='badge b-acc'>🧪 إشارة SPY تجريبية: {word}</span> "
    if s["rev"]:
        word = "صعوده" if s["rev"]["dir"] == "up" else "نزوله"
        out += (f"<span class='badge b-warn'>⚠️ احتمال ارتداد بعد {word}: "
                f"{'، '.join(s['rev']['reasons'])}</span> ")
    return out


def cards():
    penny = mode == MODES[1]
    out = get_results(penny)
    if out is None:
        st.warning("تعذر تحميل البيانات هالمرة، بيعيد المحاولة تلقائياً بعد شوي.")
        return
    results, mkt, _ = out
    if not results:
        st.warning("ما وصلت بيانات. تأكد من رموز الأسهم أو جرّب بعد دقيقة.")
        return
    mkt = mkt or {"chg": 0.0, "above_vwap": None}
    results = [dict(r) for r in results]  # نسخة للعرض، الكاش مشترك

    live = LIVE and market_hours()
    if live:
        q = live_quotes(tuple(r["sym"] for r in results))
        prev = st.session_state.setdefault("prev_px", {})
        for r in results:
            if r["sym"] in q:
                r["price"], r["chg"] = q[r["sym"]]
            old = prev.get(r["sym"])
            r["flash"] = "" if old is None or old == r["price"] else (" fu" if r["price"] > old else " fd")
            prev[r["sym"]] = r["price"]

    n_up = sum(r["status"] == "up" for r in results)
    n_dn = sum(r["status"] == "dn" for r in results)
    n_acc = sum(r["status"] == "acc" for r in results)
    what = "سهم صغير (1 إلى 5 دولار)، صعود فقط" if penny else "سهم"
    if live:
        when = (f"<span class='live'></span>مباشر · الأسعار تتحدث كل {TICK} ثواني · "
                f"{pd.Timestamp.now(tz='America/New_York').strftime('%H:%M:%S')} نيويورك. ")
    else:
        when = f"آخر تحديث: {max(r['last'] for r in results).strftime('%H:%M')} بتوقيت نيويورك. "
    st.markdown(f"<div class='sub'>{when}السوق (SPY) {mkt['chg']:+.2f}%. "
                f"<span class='up'>{n_up} صعود قوي</span>، <span class='down'>{n_dn} هبوط قوي</span>، "
                f"<span style='color:{SIG_C}'>{n_acc} تجميع</span> من {len(results)} {what}</div>",
                unsafe_allow_html=True)

    shown = [r for r in results if r["status"]] if only_sig else results
    if only_top:
        shown = [r for r in shown if r["score"] >= min_score]
    rank = {"up": 2, "dn": 2, "acc": 1, "": 0}
    shown = sorted(shown, key=lambda r: (rank[r["status"]], r[sort_key]), reverse=True)
    # SPY ثابت أول بطاقة دايماً (مؤشر السوق)، ما يتحرك مع الترتيب ولا ينخفي بالفلتر
    spy = [r for r in results if r["sym"] == rc.MARKET]
    if spy:
        shown = spy + [r for r in shown if r["sym"] != rc.MARKET]
        if not penny:
            spy[0]["spy_badge"] = spy_badge(out[2])
    if not shown:
        st.info("ما فيه أسهم تطابق الفلتر الحين. خفّض عدد النقاط المطلوب من الإعدادات أو انتظر.")
    if show_opt and shown and not penny:
        store = _opt_store()

        def job(r):
            kind = "call" if r["side"] == "up" else "put"
            try:
                if LIVE:
                    return _cached(store, (r["sym"], kind, "live"), 60,
                                   lambda: rc.best_option(r["sym"], kind, r["price"], opt_rules))
                return best_option(store, r["sym"], kind, r["price"], opt_budget, opt_dte, opt_min_oi)
            except Exception:
                return None, "error"
        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                for r, o in zip(shown, ex.map(job, shown)):
                    r["opt"] = o
        except RuntimeError:
            # السيرفر ما قدر يفتح خيوط إضافية: نجيب العقود بالتسلسل بدل ما تطيح الصفحة
            for r in shown:
                r["opt"] = r.get("opt") or job(r)
    st.markdown("".join(row_html(r) for r in shown), unsafe_allow_html=True)


def details():
    penny = mode == MODES[1]
    out = get_results(penny)
    if not out or not out[0]:
        return
    results, _, data = out
    frames, dailies = data["intr"], data["daily"]
    st.markdown("<div class='rtl' style='margin-top:1.2rem'><b>تفاصيل سهم</b></div>",
                unsafe_allow_html=True)
    options = [r["sym"] for r in sorted(results, key=lambda r: -r["score"])]
    pick_sym = st.selectbox("اختر السهم", options, key=f"pick_{penny}")
    sel = next((r for r in results if r["sym"] == pick_sym), None)
    if sel and pick_sym in frames:
        st.plotly_chart(detail_chart(frames[pick_sym], sel["sma"]), width="stretch")
        st.markdown(f"<div class='sub'>فوق: السعر وVWAP (منقط أصفر) ومتوسط 20 يوم (متقطع أزرق). "
                    f"وسط: السيولة كل دقيقة (<span style='color:{IN_C}'>داخلة</span> / "
                    f"<span style='color:{OUT_C}'>خارجة</span>). تحت: صافي السيولة من بداية اليوم.</div>",
                    unsafe_allow_html=True)
        st.markdown("<div class='rtl' style='margin-top:.8rem'><b>شارت الفريمات وخطوط الترند</b></div>",
                    unsafe_allow_html=True)
        frame = st.radio("الفريم", ["TradingView لحظي", "15 دقيقة", "ساعة", "يومي"], horizontal=True,
                         label_visibility="collapsed")
        if frame == "TradingView لحظي":
            tv_chart(pick_sym)
            st.markdown("<div class='sub'>شارت TradingView بأسعار لحظية. تقدر تغيّر الفريم وتضيف مؤشرات من داخله.</div>",
                        unsafe_allow_html=True)
        elif frame == "يومي":
            st.plotly_chart(daily_chart(dailies[pick_sym], frames[pick_sym]), width="stretch")
            st.markdown("<div class='sub'>الخط الأزرق متوسط 20 يوم.</div>", unsafe_allow_html=True)
        else:
            name = "15د" if frame == "15 دقيقة" else "ساعة"
            bars = data["b15" if name == "15د" else "b60"].get(pick_sym)
            if bars is None or len(bars) < 10:
                st.info("ما وصلت بيانات هذا الفريم.")
            else:
                st.plotly_chart(tf_chart(bars, sel["tl"][name]), width="stretch")
                st.markdown(f"<div class='sub'>المتقطع <span style='color:{OUT_C}'>الأحمر</span> ترند هابط يربط القمم، "
                            f"واختراقه لفوق إشارة صعود. والمتقطع <span style='color:{IN_C}'>الأخضر</span> ترند صاعد "
                            f"يربط القيعان، وكسره لتحت إشارة هبوط. إذا ما ظهر خط، فما فيه ترند صالح بعدد اللمسات المطلوب.</div>",
                            unsafe_allow_html=True)


MODES = ("الأسهم الكبيرة", "الأسهم الصغيرة (1 إلى 5 دولار)")
mode = st.radio("القائمة", MODES, horizontal=True, label_visibility="collapsed") if LIVE else MODES[0]

st.markdown("<div class='sub'>المؤشرات لحظية من TradingView (S&amp;P 500، ناسداك 100، ومؤشر الخوف VIX)</div>",
            unsafe_allow_html=True)
tv_indices()
st.markdown(TV_COPY, unsafe_allow_html=True)

if not tickers and mode == MODES[0]:
    st.info("أضف رموز الأسهم من الإعدادات للبدء.")
    st.stop()

c1, c2, c3 = st.columns([2, 1, 1])
sort_by = c1.selectbox("الترتيب حسب", ["قوة الإشارة", "صافي السيولة اليوم",
                                       "نسبة السيولة الداخلة", "الزخم", "الحجم مقابل المعتاد"])
only_sig = c2.toggle("الإشارات والتجميع فقط", value=False)
only_top = c3.toggle(f"الأقوى ({min_score}+ نقاط)", value=False,
                     help=f"يعرض الأسهم اللي حققت {min_score} نقاط أو أكثر، صعود أو هبوط، "
                          "حتى لو ما تحقق فيها شرط السيولة أو متوسط 20 يوم")
sort_key = {"قوة الإشارة": "score", "صافي السيولة اليوم": "net_day", "نسبة السيولة الداخلة": "in_pct",
            "الزخم": "mom", "الحجم مقابل المعتاد": "rvol"}[sort_by]

# البطاقات تتحدث كل 3 ثواني (الأسعار) والتحليل كل 15 ثانية؛ الشارتات لحالها كل دقيقة عشان ما ترمش
st.fragment(run_every=TICK if auto else None)(cards)()
st.fragment(run_every=60 if auto else None)(details)()

src = ("النقاط والسيولة والعقود لحظية من Alpaca." if LIVE else
       "النقاط والسيولة من Yahoo Finance وقد تتأخر دقيقة إلى دقيقتين.")
st.markdown(f"<div class='sub'>المؤشرات والشارت لحظية من TradingView. {src} "
            "الأرقام للمتابعة فقط وليست توصية شراء أو بيع.</div>", unsafe_allow_html=True)
