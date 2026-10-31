"""
رادار السيولة — زخم الأسهم الأمريكية، السيولة الداخلة والخارجة، ونقاط الإشارة
تشغيل محلي:  pip install -r requirements.txt  ثم  streamlit run app.py
"""
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="رادار السيولة", page_icon="📈", layout="wide")

DEFAULT_TICKERS = (
    "AAPL MSFT NVDA AMD TSLA META AMZN GOOGL NFLX AVGO PLTR SMCI COIN MSTR SOFI "
    "UBER SHOP CRWD SNOW PANW MU INTC ARM MRVL RIVN LCID NIO BABA PYPL XYZ "
    "ROKU DKNG HOOD RBLX AFRM UPST MARA RIOT CVNA ENPH"
)
MARKET = "SPY"
IN_C, OUT_C, SIG_C, MUTED = "#3CC7A6", "#EE6B5E", "#F2B544", "#8C97A8"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;700&display=swap');
html, body, .stApp, .stMarkdown, button, input, textarea, select {{
  font-family: 'IBM Plex Sans Arabic', system-ui, sans-serif !important; }}
.rtl {{ direction: rtl; text-align: right; }}
h1.title {{ direction: rtl; text-align: right; font-size: 1.9rem; font-weight: 700; margin: 0 0 .2rem; }}
.sub {{ direction: rtl; text-align: right; color: {MUTED}; font-size: .95rem; margin-bottom: 1rem; }}
.row {{ direction: rtl; text-align: right; background: #222C3C; border: 1px solid #2F3B4F;
        border-radius: 10px; padding: .8rem 1rem; margin-bottom: .55rem; }}
.row.up {{ border-color: {IN_C}; }} .row.dn {{ border-color: {OUT_C}; }} .row.acc {{ border-color: {SIG_C}; }}
.head {{ display: flex; justify-content: space-between; align-items: baseline; gap: .6rem; flex-wrap: wrap; }}
.sym {{ font-size: 1.25rem; font-weight: 700; letter-spacing: .02em; }}
.badge {{ font-size: .78rem; padding: .1rem .55rem; border-radius: 99px; margin-right: .5rem; font-weight: 500; }}
.b-up {{ background: {IN_C}; color: #18202D; }}
.b-dn {{ background: {OUT_C}; color: #18202D; }}
.b-acc {{ background: rgba(242,181,68,.18); color: {SIG_C}; }}
.price {{ font-size: 1.05rem; font-weight: 500; direction: ltr; }}
.up {{ color: {IN_C}; }} .down {{ color: {OUT_C}; }}
.bar {{ display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin: .6rem 0 .5rem; background: {OUT_C}; }}
.bar .in {{ background: {IN_C}; }}
.stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: .4rem; }}
.stat {{ text-align: right; }}
.stat .k {{ color: {MUTED}; font-size: .75rem; text-align: right; }}
.stat .v {{ font-size: .95rem; font-weight: 500; direction: ltr; text-align: right; }}
.score {{ display: flex; align-items: center; gap: .6rem; margin: .7rem 0 .4rem; }}
.score .t {{ font-size: .85rem; font-weight: 500; white-space: nowrap; }}
.meter {{ flex: 1; height: 6px; border-radius: 3px; background: #2F3B4F; overflow: hidden; }}
.meter div {{ height: 100%; }}
.checks {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: .3rem .6rem; }}
.chk {{ font-size: .78rem; color: {MUTED}; }}
.chk.ok {{ color: #E6EAF0; }}
.chk .m {{ display: inline-block; width: 1.1em; font-weight: 700; }}
.chk .d {{ color: {MUTED}; direction: ltr; unicode-bidi: isolate; }}
@media (max-width: 640px) {{
  .stats, .checks {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>
""", unsafe_allow_html=True)

# ---------------- الإعدادات ----------------
with st.sidebar:
    st.markdown("<div class='rtl'><b>الإعدادات</b></div>", unsafe_allow_html=True)
    tickers_txt = st.text_area("الأسهم (افصل بمسافة)", DEFAULT_TICKERS, height=150)
    min_score = st.slider("أقل عدد نقاط للإشارة القوية (من 8)", 4, 8, 6)
    flow_len = st.slider("فترة حساب السيولة (دقائق)", 5, 60, 15)
    rvol_min = st.slider("أقل حجم مقابل المعتاد (RVOL)", 1.0, 5.0, 2.0, 0.1)
    in_min = st.slider("سيولة داخلة قوية من %", 50, 90, 65)
    out_max = st.slider("سيولة خارجة قوية تحت %", 10, 50, 35)
    near_pct = st.slider("قرب متوسط 20 يوم (%)", 0.2, 3.0, 1.0, 0.1)
    atr_mult = st.slider("أقل حركة مقابل الحركة الطبيعية (ATR)", 0.2, 1.5, 0.5, 0.1)
    rs_min = st.slider("أقوى/أضعف من السوق بـ %", 0.0, 3.0, 1.0, 0.1)
    mom_min = st.slider("أقل زخم % خلال فترة السيولة", 0.0, 3.0, 0.5, 0.1)
    spike_min = st.slider("قفزة حجم آخر 5 دقائق للتجميع (x)", 1.0, 8.0, 3.0, 0.5)
    auto = st.toggle("تحديث تلقائي كل دقيقة", value=True)

tickers = tuple(dict.fromkeys(t.strip().upper() for t in tickers_txt.split() if t.strip()))
fetch = tuple(dict.fromkeys(tickers + (MARKET,)))


# ---------------- البيانات ----------------
@st.cache_data(ttl=55, show_spinner=False)
def load_intraday(tks):
    return yf.download(list(tks), period="1d", interval="1m", group_by="ticker",
                       prepost=False, threads=True, progress=False, auto_adjust=False)


@st.cache_data(ttl=3600, show_spinner=False)
def load_daily(tks):
    return yf.download(list(tks), period="3mo", interval="1d", group_by="ticker",
                       threads=True, progress=False, auto_adjust=False)


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


def analyze(sym, intr, daily, mkt):
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
    ]
    bear = [
        ("سيولة خارجة قوية", in_pct <= out_max, f"{100 - in_pct:.0f}%"),
        ("كسر متوسط 20 يوم", cross_dn or near_dn, ma_detail(cross_dn, near_dn, "كسر")),
        ("حجم غير معتاد", rvol >= rvol_min, f"{rvol:.1f}x"),
        ("تحت VWAP", price < vwap, ""),
        ("زخم هابط", mom <= -mom_min, f"{mom:+.2f}%"),
        ("حركة أكبر من الطبيعي", move <= -atr_mult, f"{abs(move):.1f}x"),
        ("أضعف من السوق", -rs >= rs_min, f"{rs:+.1f}%"),
        ("السوق ضاغط", mkt_dn, f"SPY {mkt['chg']:+.1f}%"),
    ]
    s_up, s_dn = sum(ok for _, ok, _ in bull), sum(ok for _, ok, _ in bear)
    side = "up" if s_up >= s_dn else "dn"
    checks, score = (bull, s_up) if side == "up" else (bear, s_dn)
    # الإشارة القوية تحتاج: النقاط الكافية + السيولة + المتوسط
    strong = score >= min_score and checks[0][1] and checks[1][1]
    accum = (rvol >= rvol_min and spike >= spike_min and abs(move) <= 0.3
             and abs(price / vwap - 1) * 100 <= 0.5)
    status = side if strong else ("acc" if accum else "")

    return dict(sym=sym, price=price, chg=chg, rvol=rvol, in_pct=in_pct,
                net_day=mf.sum(), mom=mom, sma=sma_live, side=side, score=score,
                checks=checks, status=status, unusual=rvol >= rvol_min,
                last=today.index[-1])


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
    <div><span class='sym'>{r["sym"]}</span>{badge}</div>
    <div class='price'>{r["price"]:.2f} <span class='{cc(r["chg"])}'>{r["chg"]:+.2f}%</span></div>
  </div>
  <div class='bar'><div class='in' style='width:{r["in_pct"]:.0f}%'></div></div>
  <div class='stats'>
    <div class='stat'><div class='k'>السيولة الداخلة ({flow_len}د)</div><div class='v'>{r["in_pct"]:.0f}%</div></div>
    <div class='stat'><div class='k'>صافي السيولة اليوم</div><div class='v {cc(r["net_day"])}'>{money(r["net_day"])}</div></div>
    <div class='stat'><div class='k'>الزخم ({flow_len}د)</div><div class='v {cc(r["mom"])}'>{r["mom"]:+.2f}%</div></div>
    <div class='stat'><div class='k'>الحجم مقابل المعتاد</div><div class='v'>{r["rvol"]:.1f}x</div></div>
  </div>
  <div class='score'>
    <span class='t' style='color:{side_c}'>نقاط ال{side_t}: {r["score"]}/8</span>
    <div class='meter'><div style='width:{r["score"] / 8 * 100:.0f}%;background:{side_c}'></div></div>
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
                      paper_bgcolor="#18202D", plot_bgcolor="#18202D", font=dict(color="#E6EAF0"),
                      bargap=0)
    fig.update_xaxes(gridcolor="#2F3B4F")
    fig.update_yaxes(gridcolor="#2F3B4F")
    return fig


# ---------------- الواجهة ----------------
st.markdown("<h1 class='title'>رادار السيولة</h1>", unsafe_allow_html=True)
st.markdown("<div class='sub'>نقاط الصعود والهبوط لكل سهم: السيولة، متوسط 20 يوم، الحجم، الزخم، وحركة السوق</div>",
            unsafe_allow_html=True)


def scanner():
    if not tickers:
        st.info("أضف رموز الأسهم من الإعدادات للبدء.")
        return
    with st.spinner("جاري تحميل البيانات…"):
        intr_all, daily_all = load_intraday(fetch), load_daily(fetch)

    mkt = market_state(pick(intr_all, MARKET), pick(daily_all, MARKET))
    results, frames = [], {}
    for s in tickers:
        intr = pick(intr_all, s)
        r = analyze(s, intr, pick(daily_all, s), mkt)
        if r:
            results.append(r)
            frames[s] = intr
    if not results:
        st.warning("ما وصلت بيانات. تأكد من رموز الأسهم أو جرّب بعد دقيقة.")
        return

    last = max(r["last"] for r in results)
    n_up = sum(r["status"] == "up" for r in results)
    n_dn = sum(r["status"] == "dn" for r in results)
    n_acc = sum(r["status"] == "acc" for r in results)
    st.markdown(f"<div class='sub'>آخر تحديث: {last.strftime('%H:%M')} بتوقيت نيويورك. "
                f"السوق (SPY) {mkt['chg']:+.2f}%. "
                f"<span class='up'>{n_up} صعود قوي</span>، <span class='down'>{n_dn} هبوط قوي</span>، "
                f"<span style='color:{SIG_C}'>{n_acc} تجميع</span> من {len(results)} سهم</div>",
                unsafe_allow_html=True)

    c1, c2 = st.columns([2, 1])
    sort_by = c1.selectbox("الترتيب حسب", ["قوة الإشارة", "صافي السيولة اليوم",
                                           "نسبة السيولة الداخلة", "الزخم", "الحجم مقابل المعتاد"])
    only_sig = c2.toggle("الإشارات والتجميع فقط", value=False)
    key = {"قوة الإشارة": "score", "صافي السيولة اليوم": "net_day", "نسبة السيولة الداخلة": "in_pct",
           "الزخم": "mom", "الحجم مقابل المعتاد": "rvol"}[sort_by]

    shown = [r for r in results if r["status"]] if only_sig else results
    rank = {"up": 2, "dn": 2, "acc": 1, "": 0}
    shown = sorted(shown, key=lambda r: (rank[r["status"]], r[key]), reverse=True)
    if not shown:
        st.info("ما فيه إشارات قوية الحين. خفّض عدد النقاط المطلوب من الإعدادات أو انتظر.")
    st.markdown("".join(row_html(r) for r in shown), unsafe_allow_html=True)

    st.markdown("<div class='rtl' style='margin-top:1.2rem'><b>تفاصيل سهم</b></div>",
                unsafe_allow_html=True)
    options = [r["sym"] for r in shown] or [r["sym"] for r in results]
    pick_sym = st.selectbox("اختر السهم", options)
    sel = next((r for r in results if r["sym"] == pick_sym), None)
    if sel and pick_sym in frames:
        st.plotly_chart(detail_chart(frames[pick_sym], sel["sma"]), width="stretch")
        st.markdown(f"<div class='sub'>فوق: السعر وVWAP (منقط أصفر) ومتوسط 20 يوم (متقطع أزرق). "
                    f"وسط: السيولة كل دقيقة (<span style='color:{IN_C}'>داخلة</span> / "
                    f"<span style='color:{OUT_C}'>خارجة</span>). تحت: صافي السيولة من بداية اليوم.</div>",
                    unsafe_allow_html=True)


if auto:
    st.fragment(run_every=60)(scanner)()
else:
    st.fragment(scanner)()

st.markdown("<div class='sub'>البيانات من Yahoo Finance وقد تتأخر قليلاً. "
            "الأرقام للمتابعة فقط وليست توصية شراء أو بيع.</div>", unsafe_allow_html=True)
