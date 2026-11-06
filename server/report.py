"""
تقرير نهاية الجلسة: لكل تنبيه جا اليوم، سعره وقت التنبيه وأعلى سعر بعده وسعر الإغلاق.
التنبيهات تنحفظ في STATE_DIR/alerts-YYYY-MM-DD.json وقت إرسالها، والتقرير ينبني منها بعد الإغلاق.
"""
import os
import csv
import json
import datetime as dt
from zoneinfo import ZoneInfo

import radar_core as rc
import cards

NY = rc.NY
RIYADH = ZoneInfo("Asia/Riyadh")
DAYS = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


def path(state_dir, day):
    return os.path.join(state_dir, f"alerts-{day}.json")


def load(state_dir, day):
    try:
        return json.load(open(path(state_dir, day)))
    except Exception:
        return []


def add(state_dir, day, rec):
    recs = load(state_dir, day)
    recs.append(rec)
    tmp = path(state_dir, day) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(recs, f, ensure_ascii=False)
    os.replace(tmp, path(state_dir, day))


def csv_records(state_dir, day):
    """تنبيهات اليوم من signals.csv (أول مرة لكل سهم واتجاه) — احتياط لو ملف اليوم ناقص."""
    out, seen = [], set()
    try:
        rows = list(csv.reader(open(os.path.join(state_dir, "signals.csv"), encoding="utf-8")))[1:]
    except Exception:
        return out
    for row in rows:
        try:
            ts = dt.datetime.strptime(row[0], "%Y-%m-%d %H:%M").replace(tzinfo=RIYADH).astimezone(NY)
            if str(ts.date()) != day:
                continue
            kind = row[10] if len(row) > 10 and row[10] else ("big" if row[6] else "penny")
            key = (row[1], kind, row[2])
            if key in seen:
                continue
            seen.add(key)
            rec = dict(ts=ts.isoformat(), list=kind, sym=row[1], side=row[2], score=int(row[3]), price=float(row[4]))
            if kind in ("big", "spy"):
                exp = dt.date.fromisoformat(row[8])
                strike = float(row[7])
                rec.update(kind=row[6], strike=strike, exp_label=rc.exp_label(exp, ts.date()),
                           option=f"{row[1]}{exp:%y%m%d}{row[6][0].upper()}{round(strike * 1000):08d}",
                           entry=float(row[9]) / 100)
            else:
                rec.update(entry=float(row[4]))
            out.append(rec)
        except Exception:
            continue
    return out


def records(state_dir, day):
    """ملف اليوم، ويكمّل من signals.csv أي تنبيه ناقص."""
    recs = load(state_dir, day)
    extra = {(r["sym"], r["list"], r["side"]): r for r in csv_records(state_dir, day)}
    for r in recs:  # التنبيهات القديمة ما فيها سعر السهم: ناخذه من signals.csv
        if "price" not in r and (r["sym"], r["list"], r["side"]) in extra:
            r["price"] = extra[(r["sym"], r["list"], r["side"])]["price"]
    have = {(r["sym"], r["list"], r["side"]) for r in recs}
    return recs + [r for k, r in extra.items() if k not in have]


def touch(state_dir, day):
    """يعلّم إن الرادار اشتغل اليوم (عشان يطلع تقرير حتى لو ما فيه تنبيهات)."""
    if not os.path.exists(path(state_dir, day)):
        json.dump([], open(path(state_dir, day), "w"))


def clock(t):
    t = t.astimezone(RIYADH)
    return t.strftime("%I:%M").lstrip("0") + (" م" if t.hour >= 12 else " ص")


def day_label(day):
    d = dt.date.fromisoformat(day)
    return f"{DAYS[d.weekday()]} {d.strftime('%m/%d')}"


def _option_bars(a, syms, start, end):
    raw = a.paged(f"{a.DATA}/v1beta1/options/bars",
                  dict(symbols=",".join(syms), timeframe="1Min", start=start.isoformat(),
                       end=end.isoformat(), limit=10000), "bars")
    return {s: [(dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")), b["h"], b["c"], b["l"]) for b in rows]
            for s, rows in raw.items()}


def _stock_bars(a, syms, start, end):
    frames = a.bars(syms, "1Min", start, end)
    return {s: [(t.to_pydatetime(), float(row.High), float(row.Close)) for t, row in f.iterrows()] for s, f in frames.items()}


def _after(bars, ts, entry):
    """أعلى سعر ووقته وسعر الإغلاق من الصفقات اللي بعد لحظة التنبيه."""
    rows = [b for b in bars if b[0] >= ts]
    if not rows:
        return entry, "ما تداول", entry
    t, high = max(rows, key=lambda b: b[1])[:2]
    return high, clock(t), rows[-1][2]


def outcome(bars, ts, entry, win_pct, stop_pct):
    """وش صار أول بعد التنبيه: وصل الهدف (win) أو نزل للوقف (stop)، وإلا يُحسب على الإغلاق (close).
    إذا الاثنين في نفس الدقيقة نعتبره وقف (الأحوط)."""
    for b in bars:
        if b[0] < ts:
            continue
        if b[3] <= entry * (1 - stop_pct / 100):
            return "stop"
        if b[1] >= entry * (1 + win_pct / 100):
            return "win"
    return "close"


def build_spx(recs, day):
    """تقرير SPX: يرجع (صفوف الأوامر اللي تنفذت، عدد اللي ما تنفذت). الدخول = سعر الأمر، والتنفيذ إذا نزل العقد 2% تحته."""
    spx = [r for r in recs if r["list"] in ("spx", "spx-late") and "order" in r]
    if not spx:
        return [], 0
    d = dt.date.fromisoformat(day)
    bars = _option_bars(rc.api(), sorted({r["option"] for r in spx}), dt.datetime.combine(d, dt.time(9, 30), NY),
                        dt.datetime.combine(d, dt.time(16, 0), NY))
    rows, missed = [], 0
    for r in spx:
        ts, cancel = dt.datetime.fromisoformat(r["ts"]), dt.datetime.fromisoformat(r["cancel"])
        b = bars.get(r["option"], [])
        fill = next((x[0] for x in b if ts < x[0] <= cancel and x[3] <= r["order"] * 0.98), None)
        if fill is None:
            missed += 1
            continue
        after = fill + dt.timedelta(minutes=1)
        high, high_time, close = _after(b, after, r["order"])
        rows.append(dict(sym=r["sym"], side=r["side"], kind=r["kind"], strike=r["strike"], exp_label=r["exp_label"],
                         time=clock(ts), entry=r["order"], high=high, high_time=high_time, close=close,
                         # ما فيه هدف ربح في SPX، وبدون وقف: نسب خارج المدى ما تنضرب أبداً
                         result=outcome(b, after, r["order"], 10 ** 9, cards.STOP_PCT if r["stop"] else 101)))
    return rows, missed


def build(recs, day):
    """يرجع (صفوف العقود، صفوف الأسهم الصغيرة، صفوف SPY التجريبية) جاهزة لـ cards."""
    a = rc.api()
    d = dt.date.fromisoformat(day)
    start = dt.datetime.combine(d, dt.time(9, 30), NY)
    end = dt.datetime.combine(d, dt.time(16, 0), NY)
    # العقود فقط لأسهم سعرها ضمن حدود الأسهم الكبيرة (التنبيهات القديمة بدون سعر نقيس بالسترايك)
    big = [r for r in recs if r["list"] == "big" and r["sym"] == rc.MARKET
           or r["list"] == "big" and rc.BIG_PRICE[0] <= r.get("price", r.get("strike", 0)) <= rc.BIG_PRICE[1]]
    spy = [r for r in recs if r["list"] == "spy"]
    penny = [r for r in recs if r["list"] == "penny"]
    obars = _option_bars(a, sorted({r["option"] for r in big + spy}), start, end) if big or spy else {}
    sbars = _stock_bars(a, sorted({r["sym"] for r in penny}), start, end) if penny else {}

    def contract_row(r):
        ts = dt.datetime.fromisoformat(r["ts"])
        bars = obars.get(r["option"], [])
        high, high_time, close = _after(bars, ts, r["entry"])
        return dict(sym=r["sym"], side=r["side"], kind=r["kind"], strike=r["strike"],
                    exp_label=r["exp_label"], time=clock(ts), entry=r["entry"],
                    high=high, high_time=high_time, close=close,
                    result=outcome(bars, ts, r["entry"], cards.WIN_PCT, cards.STOP_PCT))

    crow, srow, prow = [contract_row(r) for r in big], [contract_row(r) for r in spy], []
    for r in penny:
        ts = dt.datetime.fromisoformat(r["ts"])
        high, high_time, close = _after(sbars.get(r["sym"], []), ts, r["entry"])
        prow.append(dict(sym=r["sym"], score=r["score"], time=clock(ts), entry=r["entry"],
                         high=high, high_time=high_time, close=close))
    return crow, prow, srow
