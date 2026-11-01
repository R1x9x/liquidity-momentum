"""
يرسل تنبيه تجريبي لتلقرام بنفس شكل تنبيه العقود، من بيانات حقيقية (أول سهم يطلع له عقد مطابق).
تشغيل على السيرفر:  set -a; . /opt/radar/.env; set +a; /opt/radar/venv/bin/python /opt/radar/repo/server/test_alert.py
"""
import alerts

rc, cards = alerts.rc, alerts.cards


def main():
    tickers = rc.DEFAULT_TICKERS.split()
    data = rc.load_alpaca(tickers + [rc.MARKET])
    results, _ = rc.scan(data, tickers)
    for r in sorted(results, key=lambda r: -r["score"]):
        kind = "call" if r["side"] == "up" else "put"
        opt, _ = rc.best_option(r["sym"], kind, r["price"])
        if opt:
            caption = (f"🧪 رسالة تجريبية (مو إشارة حقيقية: النقاط {r['score']}/10)\n"
                       f"{'🟢 صعود' if kind == 'call' else '🔴 هبوط'} | {r['sym']} | "
                       f"{kind.upper()} ${opt['strike']:g} | {opt['exp_label']}")
            ok = alerts.send_photo(cards.contract_card(r, opt, alerts.riyadh_time()), caption)
            print("sent" if ok else "telegram failed", r["sym"], opt["symbol"])
            return
    alerts.send("🧪 رسالة تجريبية: ما لقيت الحين ولا عقد مطابق للشروط في قائمة الأسهم.")
    print("no matching contract")


if __name__ == "__main__":
    main()
