"""
خدمة تنبيهات رادار السيولة — تفحص الأسهم كل دقيقة وقت السوق وترسل تنبيهات تلقرام بالصور.
الأسهم الكبيرة: تنبيه فقط إذا النقاط 7+ وفيه عقد مطابق للشروط. الأسهم الصغيرة: صعود 7+ فقط.
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
ALERT_MIN = int(os.environ.get("ALERT_MIN_SCORE", "7"))
PENNY_ALERTS = os.environ.get("PENNY_ALERTS", "1") == "1"
STATE_DIR = os.environ.get("STATE_DIR", "/opt/radar/state")
os.makedirs(STATE_DIR, exist_ok=True)

NY = rc.NY
RIYADH = ZoneInfo("Asia/Riyadh")


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


def send_photo(png, caption):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                          data={"chat_id": CHAT, "caption": caption},
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
        sent.add(f"{r['sym']}:penny" if r["list"] == "penny" else f"{r['sym']}:{r['side']}")
    return sent


def save_sent(day, s):
    with open(sent_path(day), "w") as f:
        json.dump(sorted(s), f)


def remember(day, r, opt, kind):
    """يحفظ التنبيه لتقرير نهاية الجلسة (سعر العقد = سعر الطلب وقت التنبيه)."""
    rec = dict(ts=dt.datetime.now(NY).isoformat(), list=kind, sym=r["sym"], side=r["side"], score=int(r["score"]))
    if opt:
        rec.update(kind=opt["kind"], strike=float(opt["strike"]), exp_label=opt["exp_label"],
                   option=opt["symbol"], entry=float(opt["ask"]))
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


def send_reports(day):
    crow, prow = report.build(report.records(STATE_DIR, day), day)
    label = report.day_label(day)
    when = riyadh_time()
    ok = send_photo(cards.contract_report(crow, label, when), f"📊 تقرير العقود | نهاية جلسة {label}")
    if PENNY_ALERTS:
        ok = send_photo(cards.penny_report(prow, label, when), f"📊 تقرير الأسهم الصغيرة | نهاية جلسة {label}") and ok
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


def alert_big(tickers, sent, day):
    data = rc.load_alpaca(tickers + [rc.MARKET])
    results, mkt = rc.scan(data, tickers)
    for r in results:
        key = f"{r['sym']}:{r['side']}"
        if r["score"] < ALERT_MIN or key in sent:
            continue
        kind = "call" if r["side"] == "up" else "put"
        opt, why = rc.best_option(r["sym"], kind, r["price"])
        if not opt:
            continue  # بدون عقد مطابق ما فيه تنبيه (ونرجع نشيك الدقيقة الجاية)
        caption = (f"{'🟢 صعود' if kind == 'call' else '🔴 هبوط'} | {r['sym']} | "
                   f"{kind.upper()} ${opt['strike']:g} | {opt['exp_label']}")
        if send_photo(cards.contract_card(r, opt, riyadh_time()), caption):
            after_send(day, sent, key, r, opt, "big")
            log("alert", key, r["score"], opt["symbol"])
    return mkt, len(results)


def alert_penny(mkt, sent, day):
    results, _ = rc.penny_scan(mkt=mkt)
    for r in results:
        key = f"{r['sym']}:penny"
        if r["score"] < ALERT_MIN or key in sent:
            continue
        caption = f"🟡 سهم صغير | 🟢 صعود | {r['sym']}"
        if send_photo(cards.penny_card(r, r["split"], riyadh_time()), caption):
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
    log("starting, tickers:", len(tickers), "min score:", ALERT_MIN, "penny:", PENNY_ALERTS)
    send(f"✅ الرادار شغال على السيرفر ببيانات Alpaca اللحظية\n"
         f"يفحص {len(tickers)} سهم كل دقيقة وقت السوق، وينبهك عند {ALERT_MIN}/10 أو أكثر مع عقد مطابق."
         + ("\nوالأسهم الصغيرة (1 إلى 5 دولار): صعود فقط." if PENNY_ALERTS else ""))
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
            mkt, n = alert_big(tickers, sent, day)
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
    if sys.argv[1:2] == ["report"]:  # يدوي: python alerts.py report [YYYY-MM-DD]
        send_reports(sys.argv[2] if len(sys.argv) > 2 else dt.datetime.now(NY).strftime("%Y-%m-%d"))
    else:
        main()
