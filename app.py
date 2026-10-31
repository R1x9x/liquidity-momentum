"""
رادار السيولة — يعرض زخم الأسهم الأمريكية والسيولة الداخلة والخارجة منها
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
IN_C, OUT_C, SIG_C, MUTED = "#3CC7A6", "#EE6B5E", "#F2B544", "#8C97A8"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;700&display=swap');
html, body, .stApp, .stMarkdown, button, input, textarea, select {{
  font-family: 'IBM Plex Sans Arabic', system-ui, sans-serif !important; }}
.rtl {{ direction: rtl; text-align: right; }}
h1.title {{ direction: rtl; font-size: 1.9rem; font-weight: 700; margin: 0 0 .2rem; }}
.sub {{ direction: rtl; color: {MUTED}; font-size: .95rem; margin-bottom: 1rem; }}
.row {{ direction: rtl; background: #222C3C; border: 1px solid #2F3B4F; border-radius: 10px;
        padding: .8rem 1rem; margin-bottom: .55rem; }}
.row.sig {{ border-color: {SIG_C}; }}
.head {{ display: flex; justify-content: space-between; align-items: baseline; gap: .6rem; }}
.sym {{ font-size: 1.25rem; font-weight: 700; letter-spacing: .02em; }}
.badge {{ font-size: .78rem; padding: .1rem .55rem; border-radius: 99px; margin-right: .5rem; font-weight: 500; }}
.b-sig {{ background: {SIG_C}; color: #18202D; }}
.b-in {{ background: rgba(60,199,166,.18); color: {IN_C}; }}
.b-out {{ background: rgba(238,107,94,.18); color: {OUT_C}; }}
.price {{ font-size: 1.05rem; font-weight: 500; direction: ltr; }}
.up {{ color: {IN_C}; }} .down {{ color: {OUT_C}; }}
.bar {{ display: flex; height: 10px; border-radius: 5px; overflow: hidden; margin: .6rem 0 .5rem; background: {OUT_C}; }}
.bar .in {{ background: {IN_C}; }}
.stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: .4rem; }}
.stat .k {{ color: {MUTED}; font-size: .75rem; text-align: right; }}
.stat .v {{ font-size: .95rem; font-weight: 500; direction: ltr; text-align: right; }}
@media (max-width: 640px) {{ .stats {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>
""", unsafe_allow_html=True)

# ---------------- الإعدادات ----------------
with st.sidebar:
    st.markdown("<div class='rtl'><b>الإعدادات</b></div>", unsafe_allow_html=True)
    tickers_txt = st.text_area("الأسهم (افصل بمسافة)", DEFAULT_TICKERS, height=150)
    flow_len = st.slider("فترة حساب السيولة (دقائق)", 5, 60, 15)
    rvol_min = st.slider("أقل RVOL للسيولة غير المعتادة", 1.0, 5.0, 2.0, 0.1)
    spike_min = st.slider("أقل قفزة حجم آخر 5 دقائق (x)", 1.0, 8.0, 3.0, 0.5)
    in_min = st.slider("نسبة السيولة الداخلة القوية %", 50, 90, 65)
    out_max = st.slider("تحت هذه النسبة = سيولة خارجة %", 10, 50, 35)
    mom_min = st.slider("أقل زخم % خلال الفترة", 0.0, 3.0, 0.5, 0.1)
    auto = st.toggle("تحديث تلقائي كل دقيقة", value=True)

tickers = tuple(dict.fromkeys(t.strip().upper() for t in tickers_txt.split() if t.strip()))


# ---------------- البيانات ----------------
@st.cache_data(ttl=55, show_spinner=False)
def load_intraday(tks):
    return yf.download(list(tks), period="1d", interval="1m", group_by="ticker",
                       prepost=False, threads=True, progress=False, auto_adjust=False)


@st.cache_data(ttl=3600, show_spinner=False)
def load_daily(tks):
    return yf.download(list(tks), period="2mo", interval="1d", group_by="ticker",
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


def analyze(sym, intr, daily):
    if intr is None or daily is None or len(intr) < 10 or len(daily) < 10:
        return None
    day = intr.index[-1].date()
    intr = intr[intr.index.date == day]
    past = daily[daily.index.date < day]
    if len(past) < 10 or len(intr) < 10:
        return None
    prev_close = float(past["Close"].iloc[-1])
    avg_vol = float(past["Volume"].tail(20).mean())

    c, v = intr["Close"], intr["Volume"]
    price = float(c.iloc[-1])
    elapsed = (intr.index[-1] - intr.index[0]).total_seconds() / 60 + 1
    frac = min(max(elapsed / 390, 0.01), 1.0)
    rvol = v.sum() / (avg_vol * frac) if avg_vol > 0 else 0

    mf = money_flow(intr)
    w = mf.tail(flow_len)
    inflow, outflow = w[w > 0].sum(), -w[w < 0].sum()
    in_pct = inflow / (inflow + outflow) * 100 if inflow + outflow > 0 else 50

    ref = c.iloc[-flow_len - 1] if len(c) > flow_len else c.iloc[0]
    mom = (price / ref - 1) * 100
    base = v.iloc[:-5].tail(60).mean() * 5
    spike = v.tail(5).sum() / base if base > 0 else 0
    typical = (intr["High"] + intr["Low"] + c) / 3
    vwap = float((typical * v).cumsum().iloc[-1] / v.cumsum().iloc[-1])

    unusual = rvol >= rvol_min
    entry = (unusual and spike >= spike_min and in_pct >= in_min and mom >= mom_min
             and price > vwap and price > prev_close and price >= intr["High"].max() * 0.99)
    if entry:
        status = "sig"
    elif unusual and in_pct <= out_max:
        status = "out"
    elif unusual and in_pct >= in_min:
        status = "in"
    else:
        status = ""
    return dict(sym=sym, price=price, chg=(price / prev_close - 1) * 100, rvol=rvol,
                spike=spike, in_pct=in_pct, net_win=w.sum(), net_day=mf.sum(),
                mom=mom, above_vwap=price > vwap, status=status, unusual=unusual,
                last=intr.index[-1])


def money(x):
    s = "+" if x >= 0 else "−"
    x = abs(x)
    for div, u in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= div:
            return f"{s}${x / div:.1f}{u}"
    return f"{s}${x:.0f}"


BADGES = {"sig": ("b-sig", "إشارة دخول"), "in": ("b-in", "سيولة داخلة قوية"),
          "out": ("b-out", "سيولة خارجة")}


def row_html(r):
    badge = ""
    if r["status"]:
        cls, txt = BADGES[r["status"]]
        badge = f"<span class='badge {cls}'>{txt}</span>"
    chg_cls = "up" if r["chg"] >= 0 else "down"
    mom_cls = "up" if r["mom"] >= 0 else "down"
    net_cls = "up" if r["net_day"] >= 0 else "down"
    return f"""
<div class='row {"sig" if r["status"] == "sig" else ""}'>
  <div class='head'>
    <div><span class='sym'>{r["sym"]}</span>{badge}</div>
    <div class='price'>{r["price"]:.2f} <span class='{chg_cls}'>{r["chg"]:+.2f}%</span></div>
  </div>
  <div class='bar'><div class='in' style='width:{r["in_pct"]:.0f}%'></div></div>
  <div class='stats'>
    <div class='stat'><div class='k'>السيولة الداخلة ({flow_len}د)</div><div class='v'>{r["in_pct"]:.0f}%</div></div>
    <div class='stat'><div class='k'>صافي السيولة اليوم</div><div class='v {net_cls}'>{money(r["net_day"])}</div></div>
    <div class='stat'><div class='k'>الزخم ({flow_len}د)</div><div class='v {mom_cls}'>{r["mom"]:+.2f}%</div></div>
    <div class='stat'><div class='k'>الحجم مقابل المعتاد</div><div class='v'>{r["rvol"]:.1f}x</div></div>
  </div>
</div>"""


def detail_chart(intr):
    day = intr.index[-1].date()
    d = intr[intr.index.date == day]
    mf = money_flow(d)
    typical = (d["High"] + d["Low"] + d["Close"]) / 3
    vwap = (typical * d["Volume"]).cumsum() / d["Volume"].cumsum()
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.5, 0.25, 0.25],
                        vertical_spacing=0.04)
    fig.add_trace(go.Scatter(x=d.index, y=d["Close"], name="السعر", line=dict(color="#E6EAF0", width=1.6)), 1, 1)
    fig.add_trace(go.Scatter(x=d.index, y=vwap, name="VWAP", line=dict(color=SIG_C, width=1, dash="dot")), 1, 1)
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
st.markdown("<div class='sub'>زخم الأسهم الأمريكية والسيولة الداخلة والخارجة منها، دقيقة بدقيقة</div>",
            unsafe_allow_html=True)


def scanner():
    if not tickers:
        st.info("أضف رموز الأسهم من الإعدادات للبدء.")
        return
    with st.spinner("جاري تحميل البيانات…"):
        intr_all, daily_all = load_intraday(tickers), load_daily(tickers)

    results, frames = [], {}
    for s in tickers:
        intr = pick(intr_all, s)
        r = analyze(s, intr, pick(daily_all, s))
        if r:
            results.append(r)
            frames[s] = intr
    if not results:
        st.warning("ما وصلت بيانات. تأكد من رموز الأسهم أو جرّب بعد دقيقة.")
        return

    last = max(r["last"] for r in results)
    sigs = [r for r in results if r["status"] == "sig"]
    st.markdown(f"<div class='sub'>آخر تحديث للبيانات: {last.strftime('%H:%M')} بتوقيت نيويورك "
                f"— {len(sigs)} إشارة دخول من {len(results)} سهم</div>", unsafe_allow_html=True)

    c1, c2 = st.columns([2, 1])
    sort_by = c1.selectbox("الترتيب حسب", ["صافي السيولة اليوم", "نسبة السيولة الداخلة",
                                           "الزخم", "الحجم مقابل المعتاد"])
    only_unusual = c2.toggle("السيولة غير المعتادة فقط", value=False)
    key = {"صافي السيولة اليوم": "net_day", "نسبة السيولة الداخلة": "in_pct",
           "الزخم": "mom", "الحجم مقابل المعتاد": "rvol"}[sort_by]

    shown = [r for r in results if r["unusual"]] if only_unusual else results
    shown = sorted(shown, key=lambda r: (r["status"] == "sig", r[key]), reverse=True)
    if not shown:
        st.info("ما فيه أسهم سيولتها غير معتادة الحين. خفّض حد RVOL من الإعدادات أو انتظر.")
    st.markdown("".join(row_html(r) for r in shown), unsafe_allow_html=True)

    st.markdown("<div class='rtl' style='margin-top:1.2rem'><b>تفاصيل سهم</b></div>",
                unsafe_allow_html=True)
    pick_sym = st.selectbox("اختر السهم", [r["sym"] for r in shown] or [r["sym"] for r in results])
    if pick_sym in frames:
        st.plotly_chart(detail_chart(frames[pick_sym]), use_container_width=True)
        st.markdown(f"<div class='sub'>فوق: السعر وVWAP. وسط: السيولة كل دقيقة "
                    f"(<span style='color:{IN_C}'>داخلة</span> / <span style='color:{OUT_C}'>خارجة</span>). "
                    f"تحت: صافي السيولة من بداية اليوم.</div>", unsafe_allow_html=True)


if auto:
    st.fragment(run_every=60)(scanner)()
else:
    st.fragment(scanner)()

st.markdown("<div class='sub'>البيانات من Yahoo Finance وقد تتأخر قليلاً. "
            "الأرقام للمتابعة فقط وليست توصية شراء أو بيع.</div>", unsafe_allow_html=True)
