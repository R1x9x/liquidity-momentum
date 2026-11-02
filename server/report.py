"""
تقرير نهاية الجلسة: لكل تنبيه جا اليوم، سعره وقت التنبيه وأعلى سعر بعده وسعر الإغلاق.
التنبيهات تنحفظ في STATE_DIR/alerts-YYYY-MM-DD.json وقت إرسالها، والتقرير ينبني منها بعد الإغلاق.
"""
import os
import json
import datetime as dt
from zoneinfo import ZoneInfo

import radar_core as rc

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
    json.dump(recs, open(path(state_dir, day), "w"), ensure_ascii=False)


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
    return {s: [(dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")), b["h"], b["c"]) for b in rows]
            for s, rows in raw.items()}


def _stock_bars(a, syms, start, end):
    frames = a.bars(syms, "1Min", start, end)
    return {s: [(t.to_pydatetime(), float(row.High), float(row.Close)) for t, row in f.iterrows()] for s, f in frames.items()}


def _after(bars, ts, entry):
    """أعلى سعر ووقته وسعر الإغلاق من الصفقات اللي بعد لحظة التنبيه."""
    rows = [b for b in bars if b[0] >= ts]
    if not rows:
        return entry, "ما تداول", entry
    t, high, _ = max(rows, key=lambda b: b[1])
    return high, clock(t), rows[-1][2]


def build(recs, day):
    """يرجع (صفوف العقود، صفوف الأسهم الصغيرة) جاهزة لـ cards.contract_report / penny_report."""
    a = rc.api()
    d = dt.date.fromisoformat(day)
    start = dt.datetime.combine(d, dt.time(9, 30), NY)
    end = dt.datetime.combine(d, dt.time(16, 0), NY)
    big = [r for r in recs if r["list"] == "big"]
    penny = [r for r in recs if r["list"] == "penny"]
    obars = _option_bars(a, sorted({r["option"] for r in big}), start, end) if big else {}
    sbars = _stock_bars(a, sorted({r["sym"] for r in penny}), start, end) if penny else {}

    crow, prow = [], []
    for r in big:
        ts = dt.datetime.fromisoformat(r["ts"])
        high, high_time, close = _after(obars.get(r["option"], []), ts, r["entry"])
        crow.append(dict(sym=r["sym"], side=r["side"], kind=r["kind"], strike=r["strike"],
                         exp_label=r["exp_label"], time=clock(ts), entry=r["entry"],
                         high=high, high_time=high_time, close=close))
    for r in penny:
        ts = dt.datetime.fromisoformat(r["ts"])
        high, high_time, close = _after(sbars.get(r["sym"], []), ts, r["entry"])
        prow.append(dict(sym=r["sym"], score=r["score"], time=clock(ts), entry=r["entry"],
                         high=high, high_time=high_time, close=close))
    return crow, prow
