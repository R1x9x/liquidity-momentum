"""
خدمة تنبيهات رادار السيولة — تفحص الأسهم كل دقيقة وقت السوق وترسل تنبيهات تلقرام بالصور.
الأسهم الكبيرة (سعرها 60$ إلى 1500$): تنبيه فقط إذا النقاط وصلت الحد وفيه عقد مطابق للشروط. الأسهم الصغيرة: صعود 7+ فقط.
"""
import os
import sys
import csv
import json
import time
import datetime as dt
from zoneinfo import ZoneInfo

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import radar_core as rc  # noqa: E402
import cards  # noqa: E402
import report  # noqa: E402

TOKEN = os.environ["TG_TOKEN"]
CHAT = os.environ["TG_CHAT_ID"]
# بوت ثاني للأسهم الصغيرة (تنبيهاتها وتقريرها). بدونه تروح للبوت الأساسي
PENNY_BOT = (os.environ.get("PENNY_TG_TOKEN") or TOKEN, os.environ.get("PENNY_TG_CHAT_ID") or CHAT)
MAIN_BOT = (TOKEN, CHAT)
SPX_BOT = PENNY_BOT  # البوت الثاني صار خاص بعقود SPX (تنبيهاتها وتقريرها)
ALERT_MIN = int(os.environ.get("CONTRACT_MIN_SCORE", "7"))  # العقود
PENNY_MIN = int(os.environ.get("PENNY_MIN_SCORE", "7"))  # الأسهم الصغيرة لها حد مستقل
# بوت الأسهم الصغيرة موقف حالياً بطلب المستخدم. اسم جديد عشان PENNY_ALERTS=1 القديم في .env السيرفر ما يشغله
PENNY_ALERTS = os.environ.get("PENNY_ALERTS_ON", "0") == "1"
SPY_ALERTS = os.environ.get("SPY_ALERTS", "1") == "1"  # إشارة SPY التجريبية (زخم + حجم + اتساع السوق)
SPX_ALERTS = os.environ.get("SPX_ALERTS", "1") == "1"  # عقود مؤشر SPX
STATE_DIR = os.environ.get("STATE_DIR", "/opt/radar/state")
os.makedirs(STATE_DIR, exist_ok=True)

NY = rc.NY
RIYADH = ZoneInfo("Asia/Riyadh")


def log(*a):
    print(dt.datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def send(text, bot=MAIN_BOT):
    try:
        r = requests.post(f"https://api.telegram.org/bot{bot[0]}/sendMessage",
                          data={"chat_id": bot[1], "text": text}, timeout=20)
        if not r.ok:
            log("telegram error", r.status_code, r.text[:200])
    except Exception as e:
        log("telegram exception", e)


def send_photo(png, caption, bot=MAIN_BOT):
    try:
        r = requests.post(f"https://api.telegram.org/bot{bot[0]}/sendPhoto",
                          data={"chat_id": bot[1], "caption": caption},
                          files={"photo": ("radar.png", png, "image/png")}, timeout=30)
        if not r.ok:
            log("telegram photo error", r.status_code, r.text[:200])
        return r.ok
    except Exception as e:
        log("telegram photo exception", e)
        return False


def market_open(now_ny):
    if now_ny.weekday() >= 5:
        return False
    t = now_ny.hour * 60 + now_ny.minute
    return 9 * 60 + 30 <= t < 16 * 60


def riyadh_time():
    t = dt.datetime.now(RIYADH)
    return t.strftime("%I:%M").lstrip("0") + (" م" if t.hour >= 12 else " ص")


def sent_path(day):
    return os.path.join(STATE_DIR, f"sent-{day}.json")


def load_sent(day):
    try:
        sent = set(json.load(open(sent_path(day))))
    except Exception:
        sent = set()
    # كل تنبيه انرسل اليوم ومسجل في signals.csv ما يتكرر حتى لو ملف sent ما انحفظ
    for r in report.csv_records(STATE_DIR, day):
        sent.add(f"{r['sym']}:{r['list']}" if r["list"] == "penny"
                 else f"SPY:spy-{r['side']}" if r["list"] == "spy"
                 else f"SPX:{r['list']}-{r['side']}" if r["list"] in ("spx", "spx-late")
                 else f"{r['sym']}:{r['side']}")
    return sent


def save_sent(day, s):
    with open(sent_path(day), "w") as f:
        json.dump(sorted(s), f)


def details(r, min_score):
    """الشروط العشرة (✓/·) وأرقامها لحظة التنبيه — عشان نقارن الرابحة والخاسرة بعدين."""
    vwap = r.get("vwap")
    return dict(checks=[dict(name=n, ok=bool(ok), detail=d) for n, ok, d in r["checks"]],
                metrics=dict(chg=round(float(r["chg"]), 2), rvol=round(float(r["rvol"]), 2),
                             in_pct=round(float(r["in_pct"]), 1), mom=round(float(r["mom"]), 2),
                             vwap_dist=round((float(r["price"]) / vwap - 1) * 100, 2) if vwap else None,
                             sma_dist=round((float(r["price"]) / float(r["sma"]) - 1) * 100, 2),
                             flow_len=rc.PARAMS["flow_len"], min_score=min_score))


def remember(day, r, opt, kind):
    """يحفظ التنبيه لتقرير نهاية الجلسة (سعر العقد = سعر الطلب وقت التنبيه)."""
    rec = dict(ts=dt.datetime.now(NY).isoformat(), list=kind, sym=r["sym"], side=r["side"], score=int(r["score"]),
               price=round(float(r["price"]), 4))
    rec.update(details(r, PENNY_MIN if kind == "penny" else ALERT_MIN), zone=r.get("zone"))
    if opt:
        rec.update(kind=opt["kind"], strike=float(opt["strike"]), exp_label=opt["exp_label"],
                   option=opt["symbol"], entry=float(opt["ask"]),
                   opt_info=dict(dte=opt["dte"], delta=round(float(opt["delta"]), 3), gamma=round(float(opt["gamma"]), 4),
                                 theta=round(float(opt["theta"]), 4), spread=round(float(opt["spread"]), 2),
                                 iv=round(float(opt["iv"]), 1), volume=int(opt["volume"]), oi=int(opt["oi"])))
        if "order" in opt:  # SPX: أمر شراء تحت سعر التنبيه
            rec.update(order=round(float(opt["order"]), 2), cancel=opt["cancel"].isoformat(), stop=opt["stop"])
    else:
        rec.update(entry=round(float(r["price"]), 4))
    report.add(STATE_DIR, day, rec)


def after_send(day, sent, key, r, opt, kind):
    """نعلّم التنبيه كمُرسَل أول شي، عشان أي خطأ في السجلات ما يخليه يتكرر."""
    sent.add(key)
    save_sent(day, sent)
    try:
        log_signal(r, opt, kind)
        remember(day, r, opt, kind)
    except Exception as e:
        log("record error", key, e)


def send_spx_report(day):
    """يطلع بس في الأيام اللي جا فيها تنبيه SPX."""
    rows, missed = report.build_spx(report.records(STATE_DIR, day), day)
    if not rows:
        return True
    label = report.day_label(day)
    return send_photo(cards.contract_report(rows, label, riyadh_time(), title="تقرير SPX", target=False),
                      f"📊 تقرير SPX | نهاية جلسة {label}\nأوامر تنفذت: {len(rows) - missed} | ما تنفذت: {missed}", SPX_BOT)


def send_reports(day):
    crow, prow, srow = report.build(report.records(STATE_DIR, day), day)
    label = report.day_label(day)
    when = riyadh_time()
    ok = send_photo(cards.contract_report(crow, label, when), f"📊 تقرير العقود | نهاية جلسة {label}")
    ok = send_spx_report(day) and ok
    if srow:  # يطلع بس في الأيام اللي جت فيها إشارة SPY تجريبية
        ok = send_photo(cards.contract_report(srow, label, when, title="تقرير SPY التجريبي"),
                        f"🧪 تقرير إشارة SPY التجريبية | نهاية جلسة {label}") and ok
    if PENNY_ALERTS:
        ok = send_photo(cards.penny_report(prow, label, when), f"📊 تقرير الأسهم الصغيرة | نهاية جلسة {label}",
                        PENNY_BOT) and ok
    return ok


def maybe_report(now_ny):
    """بعد الإغلاق بخمس دقايق، مرة وحدة لكل يوم اشتغل فيه الرادار."""
    day = now_ny.strftime("%Y-%m-%d")
    done = os.path.join(STATE_DIR, f"report-{day}.done")
    if (now_ny.hour, now_ny.minute) < (16, 5) or os.path.exists(done) \
            or not os.path.exists(report.path(STATE_DIR, day)):
        return
    try:
        if send_reports(day):
            open(done, "w").close()
            log("report sent", day)
    except Exception as e:
        log("report error", e)
        open(done, "w").close()  # لا نكرر المحاولة كل دقيقة
        send("⚠️ ما قدرت أطلع تقرير نهاية الجلسة اليوم.")


def log_signal(r, opt, kind):
    path = os.path.join(STATE_DIR, "signals.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time_riyadh", "symbol", "side", "score", "price", "change_pct",
                        "option", "strike", "expiry", "option_cost", "list"])
        w.writerow([dt.datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M"), r["sym"], r["side"], r["score"],
                    round(r["price"], 2), round(r["chg"], 2),
                    opt["kind"] if opt else "", opt["strike"] if opt else "",
                    opt["exp"].strftime("%Y-%m-%d") if opt else "", round(opt["cost"]) if opt else "", kind])


def rev_warning(spy, side):
    """تحذير إذا SPY ممتد بنفس اتجاه التنبيه وفيه أسباب ارتداد: (عنوان، [أسباب]) أو None."""
    if not spy or not spy["rev"] or spy["rev"]["dir"] != side:
        return None
    return f"احتمال ارتداد السوق بعد {'صعوده' if side == 'up' else 'نزوله'}", spy["rev"]["reasons"]


def alert_big(tickers, sent, day):
    data = rc.load_alpaca(tickers + [rc.MARKET])
    results, mkt = rc.scan(data, tickers)
    try:
        spy = rc.spy_state(data, tickers)
    except Exception as e:
        log("spy state error", e)
        spy = None
    for r in results:
        key = f"{r['sym']}:{r['side']}"
        # SPY له إشارة خاصة (alert_spy)، النقاط العشر ما تناسبه
        if r["sym"] == rc.MARKET or r["score"] < ALERT_MIN or key in sent or not rc.big_price_ok(r):
            continue
        try:
            r["zone"] = rc.stock_zone(r["sym"], r["side"], data["intr"].get(r["sym"]), data["daily"].get(r["sym"]))
        except Exception as e:
            log("zone error", key, e)
            continue
        if not r["zone"]:
            continue  # بدون تأكيد من مناطق السيولة ما فيه تنبيه (ونرجع نشيك الدقيقة الجاية)
        kind = "call" if r["side"] == "up" else "put"
        opt, why = rc.best_option(r["sym"], kind, r["price"])
        if not opt:
            continue  # بدون عقد مطابق ما فيه تنبيه (ونرجع نشيك الدقيقة الجاية)
        zone_text = rc.ZONE_TEXT[(r["side"], r["zone"])]
        caption = (f"{'🟢 صعود' if kind == 'call' else '🔴 هبوط'} | {r['sym']} | "
                   f"{kind.upper()} ${opt['strike']:g} | {opt['exp_label']}\n{zone_text}")
        # تحذير الارتداد على SPY بس: اختبار 58 يوم ما لقى إنه يقلل خسائر عقود الشركات
        if send_photo(cards.contract_card(dict(r, zone_text=zone_text), opt, riyadh_time()), caption):
            after_send(day, sent, key, r, opt, "big")
            log("alert", key, r["score"], opt["symbol"])
    return mkt, len(results), spy, data


def alert_spx(data, sent, day):
    """عقود SPX: أول إشارة 5 من 8 في كل فترة ولكل اتجاه، مرة باليوم. بدون عقد مطابق تضيع الإشارة (زي الاختبار)."""
    now = dt.datetime.now(NY)
    slot = rc.spx_slot(now)
    if not slot:
        return
    for side in ("up", "dn"):
        name = "spx" if slot == "main" else "spx-late"
        key = f"SPX:{name}-{side}"
        if key in sent:
            continue
        r = rc.spx_signal(data, side)
        if not r or r["score"] < rc.SPX_RULES["min_score"]:
            continue
        kind = "call" if side == "up" else "put"
        opt, why = rc.spx_option(kind, r["price"], slot, now=now)
        if why == "error":
            continue  # خطأ اتصال: نرجع نشيك الدقيقة الجاية
        if not opt:
            log("spx no contract", key, why)
            sent.add(key)
            save_sent(day, sent)
            continue
        cancel = opt["cancel"].astimezone(RIYADH)
        cancel_time = cancel.strftime("%I:%M").lstrip("0") + (" م" if cancel.hour >= 12 else " ص")
        caption = (f"{'🟢 صعود' if kind == 'call' else '🔴 هبوط'} | SPX | {kind.upper()} ${opt['strike']:,.0f} | "
                   f"{opt['exp_label']}\nنطاق الشراء ${round(opt['low'] * 100):,} - ${round(opt['order'] * 100):,}"
                   + ("" if opt["stop"] else " | بدون وقف"))
        if send_photo(cards.spx_card(r, opt, riyadh_time(), cancel_time), caption, SPX_BOT):
            after_send(day, sent, key, r, opt, name)
            log("spx alert", key, r["score"], opt["symbol"])


def follow_path(day):
    return os.path.join(STATE_DIR, f"spx-follow-{day}.json")


def follow_spx(day):
    """بعد تنفيذ أمر SPX: تنبيه كل ما سجل العقد سعر أعلى من آخر سعر انرسل، لين الوقف أو الإغلاق."""
    recs = [r for r in report.load(STATE_DIR, day) if r["list"] in ("spx", "spx-late") and "order" in r]
    if not recs:
        return
    try:
        state = json.load(open(follow_path(day)))
    except Exception:
        state = {}
    live = [r for r in recs if not state.get(f"{r['list']}:{r['side']}", {}).get("done")]
    if not live:
        return
    now = dt.datetime.now(NY)
    a = rc.api()
    syms = sorted({r["option"] for r in live})
    bars = report._option_bars(a, syms, dt.datetime.combine(now.date(), dt.time(9, 30), NY), now)
    snaps = a.paged(f"{a.DATA}/v1beta1/options/snapshots", dict(symbols=",".join(syms), feed="opra", limit=1000), "snapshots")
    for r in live:
        key = f"{r['list']}:{r['side']}"
        st = state.setdefault(key, dict(top=r["order"], done=False))
        ts, cancel = dt.datetime.fromisoformat(r["ts"]), dt.datetime.fromisoformat(r["cancel"])
        b = bars.get(r["option"], [])
        fill = next((x[0] for x in b if ts < x[0] <= cancel and x[3] <= r["order"] * 0.98), None)
        if fill is None:
            st["done"] = now > cancel  # الأمر ما تنفذ: ما فيه متابعة
            continue
        if r["stop"] and any(x[0] > fill and x[3] <= r["order"] * 0.5 for x in b):
            st["done"] = True
            continue
        price = float(((snaps.get(r["option"]) or {}).get("latestTrade") or {}).get("p") or 0)
        if price > st["top"]:
            caption = f"📈 SPX {r['kind'].upper()} ${r['strike']:,.0f} | ${price * 100:,.0f}"
            if send_photo(cards.spx_rise_card(r["kind"], r["strike"], r["exp_label"], price, riyadh_time()), caption, SPX_BOT):
                st["top"] = price
                log("spx rise", key, price)
    with open(follow_path(day), "w") as f:
        json.dump(state, f)


def alert_spy(spy, sent, day):
    """إشارة SPY التجريبية: الشروط الثلاثة مع بعض + عقد مطابق، مرة باليوم لكل اتجاه."""
    if not spy or not spy["signal"]:
        return
    key = f"SPY:spy-{spy['signal']}"
    if key in sent:
        return
    kind = "call" if spy["signal"] == "up" else "put"
    # الإشارة التجريبية تبقى على مدى تكلفتها، وسقف الشركات في rc.OPT_RULES
    opt, why = rc.best_option(rc.MARKET, kind, spy["price"], dict(rc.OPT_RULES, budget=(100, 400)))
    if not opt:
        return
    sig = dict(spy, side=spy["signal"])
    warn = rev_warning(spy, spy["signal"])
    caption = (f"🧪 SPY تجريبي | {'🟢 صعود' if kind == 'call' else '🔴 هبوط'} | "
               f"{kind.upper()} ${opt['strike']:g} | {opt['exp_label']}") + (f"\n⚠️ {warn[0]}" if warn else "")
    if send_photo(cards.contract_card(sig, opt, riyadh_time(), trial=True, warning=warn), caption):
        after_send(day, sent, key, sig, opt, "spy")
        log("spy alert", key, opt["symbol"])


def alert_penny(mkt, sent, day):
    results, data = rc.penny_scan(mkt=mkt)
    for r in results:
        key = f"{r['sym']}:penny"
        if r["score"] < PENNY_MIN or key in sent:
            continue
        caption = f"🟡 سهم صغير | 🟢 صعود | {r['sym']}"
        try:
            info = rc.penny_extra(r["sym"], data["daily"].get(r["sym"]), data["intr"].get(r["sym"]))
        except Exception as e:
            log("penny extra error", r["sym"], e)
            info = None
        if send_photo(cards.penny_card(r, r["split"], riyadh_time(), info), caption, PENNY_BOT):
            after_send(day, sent, key, r, None, "penny")
            log("penny alert", key, r["score"])
    return len(results)


def main():
    tickers = os.environ.get("TICKERS", "").split() or rc.DEFAULT_TICKERS.split()
    if rc.data_source() != "alpaca":
        log("no Alpaca keys: alerts need live option data, waiting")
        send("⚠️ الرادار ما لقى مفاتيح Alpaca، والتنبيهات متوقفة لين تنضاف.")
        while rc.data_source() != "alpaca":
            time.sleep(300)
    log("starting, tickers:", len(tickers), "min score:", ALERT_MIN, "penny:", PENNY_ALERTS, PENNY_MIN)
    send(f"✅ الرادار شغال على السيرفر ببيانات Alpaca اللحظية\n"
         f"يفحص {len(tickers)} سهم كل دقيقة وقت السوق، وينبهك عند {ALERT_MIN}/10 أو أكثر مع عقد مطابق."
         + ("\nوإشارة SPY التجريبية (زخم + حجم + اتساع السوق) من 6 المغرب إلى 11 الليل." if SPY_ALERTS else "")
         + ("\nوعقود SPX تجيك في البوت الثاني." if SPX_ALERTS and SPX_BOT != MAIN_BOT else "")
         + (f"\nوالأسهم الصغيرة (1 إلى 5 دولار): صعود فقط عند {PENNY_MIN}/10 أو أكثر"
            + (" — تجيك في بوت الأسهم." if PENNY_BOT != MAIN_BOT else "." ) if PENNY_ALERTS else ""))
    if SPX_ALERTS and SPX_BOT != MAIN_BOT:
        send("✅ هذا البوت صار خاص بعقود SPX\nتجيك هنا تنبيهات SPX عند 5/8 أو أكثر من 5:40 م إلى 11:45 م وتقريرها آخر الجلسة.\n"
             "تنبيهات عقود الشركات تبقى في البوت الأساسي.", SPX_BOT)
    if PENNY_ALERTS and PENNY_BOT != MAIN_BOT:
        send(f"✅ بوت الأسهم الصغيرة شغال\nتجيك هنا تنبيهات الأسهم من 1 إلى 5 دولار (صعود عند {PENNY_MIN}/10 أو أكثر)"
             f" وتقريرها آخر الجلسة. تنبيهات العقود وتقريرها تبقى في البوت الأساسي.", PENNY_BOT)
    while True:
        try:
            now_ny = dt.datetime.now(NY)
            if not market_open(now_ny):
                maybe_report(now_ny)
                time.sleep(60)
                continue
            day = now_ny.strftime("%Y-%m-%d")
            report.touch(STATE_DIR, day)
            sent = load_sent(day)
            mkt, n, spy, data = alert_big(tickers, sent, day)
            if SPX_ALERTS:
                try:
                    alert_spx(data, sent, day)
                    follow_spx(day)
                except Exception as e:
                    log("spx alert error", e)
            if SPY_ALERTS:
                try:
                    alert_spy(spy, sent, day)
                except Exception as e:
                    log("spy alert error", e)
            m = 0
            if PENNY_ALERTS:
                try:
                    m = alert_penny(mkt, sent, day)
                except Exception as e:
                    log("penny error", e)
            log("scan ok", n, "stocks,", m, "penny")
        except Exception as e:
            log("loop error", e)
        time.sleep(max(5, 60 - dt.datetime.now().second))


if __name__ == "__main__":
    if sys.argv[1:2] == ["report-spx"]:  # يدوي: python alerts.py report-spx YYYY-MM-DD
        print("sent" if send_spx_report(sys.argv[2]) else "failed")
    elif sys.argv[1:2] == ["report"]:  # يدوي: python alerts.py report [YYYY-MM-DD]
        send_reports(sys.argv[2] if len(sys.argv) > 2 else dt.datetime.now(NY).strftime("%Y-%m-%d"))
    else:
        main()
