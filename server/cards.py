"""Alert cards rendered as PNG images for Telegram (Arabic, RTL, dark theme)."""
import io
import os
import re

from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
try:
    from bidi import get_display
except ImportError:
    from bidi.algorithm import get_display

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, "fonts")

BG = "#0A0A0C"
CARD = "#141519"
TILE = "#1C1D22"
LINE = "#26272E"
TEXT = "#E6EAF0"
MUTED = "#8A96A8"
UP = "#3CC7A6"
DN = "#EE6B5E"
SIGNAL = "#F2B544"

W = 1080
PAD = 48


def font(weight, size):
    # BASIC layout: we shape and reorder Arabic ourselves; with libraqm (on the server) it would be reversed twice
    return ImageFont.truetype(os.path.join(FONTS, f"IBMPlexSansArabic-{weight}.ttf"), size,
                              layout_engine=ImageFont.Layout.BASIC)


F = {
    "ticker": font("Bold", 76),
    "big": font("Bold", 60),
    "score": font("Bold", 64),
    "h2": font("Bold", 34),
    "val": font("Bold", 38),
    "body": font("Medium", 31),
    "small": font("Regular", 26),
    "pill": font("Bold", 28),
}


def ar(text):
    # an LRM before signed numbers/percents keeps them in order inside RTL text
    text = re.sub(r"([+\-]?[\d.,]+%)", "\u200e\\1", text).replace("15د", "15 دقيقة")
    return get_display(arabic_reshaper.reshape(text)).replace("\u200e", "")


def tw(d, text, f):
    return d.textlength(ar(text), font=f)


def rtext(d, x_right, y, text, f, fill=TEXT):
    """Right-aligned text; returns its width."""
    s = ar(text)
    d.text((x_right, y), s, font=f, fill=fill, anchor="ra")
    return d.textlength(s, font=f)


def ctext(d, cx, y, text, f, fill=TEXT):
    d.text((cx, y), ar(text), font=f, fill=fill, anchor="ma")


def pill(d, x_right, y, text, fill, fg=BG, f=None, h=50):
    """Rounded label anchored at its right edge; returns its width."""
    f = f or F["pill"]
    w = tw(d, text, f) + 40
    d.rounded_rectangle((x_right - w, y, x_right, y + h), radius=h // 2, fill=fill)
    ctext(d, x_right - w / 2, y + (h - f.size) / 2 - 4, text, f, fg)
    return w


def score_ring(d, cx, cy, score, total, color):
    r = 78
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=LINE, width=12)
    d.arc((cx - r, cy - r, cx + r, cy + r), -90, -90 + 360 * score / total, fill=color, width=12)
    d.text((cx, cy - 6), str(score), font=F["score"], fill=TEXT, anchor="mm")
    d.text((cx, cy + 42), f"/{total}", font=F["small"], fill=MUTED, anchor="mm")


def logo(img, x_right, y, size):
    try:
        lg = Image.open(os.path.join(HERE, "logo.webp")).convert("RGBA").resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
        img.paste(lg, (x_right - size, y), mask)
        return size
    except Exception:
        return 0



def brand(img, y=0):
    """شعار الدب والثور واسم BINSAMHA RADAR أعلى بطاقة التنبيه."""
    try:
        b = Image.open(os.path.join(HERE, "brand.png")).convert("RGB")
        img.paste(b, ((W - b.width) // 2, y))
        return y + b.height + 24
    except Exception:
        return y


def header(img, d, y, sym, badges, score, total, color, sub=None):
    xr = W - PAD
    lw = logo(img, xr, y + 6, 96)
    tx = xr - lw - (24 if lw else 0)
    d.text((tx, y - 8), sym, font=F["ticker"], fill=TEXT, anchor="ra")
    by = y + 92
    for text, fill, fg in badges:
        tx -= pill(d, tx, by, text, fill, fg) + 12
    score_ring(d, PAD + 84, y + 84, score, total, color)
    if sub:
        sub(by + 70)
    return y + 200


def tiles(d, y, items, x0=PAD + 28, x1=W - PAD - 28, h=118):
    gap = 16
    n = len(items)
    w = (x1 - x0 - gap * (n - 1)) / n
    for i, (label, value, color, *sub) in enumerate(items):
        xr = x1 - i * (w + gap)
        d.rounded_rectangle((xr - w, y, xr, y + h), radius=18, fill=TILE)
        ctext(d, xr - w / 2, y + 14, label, F["small"], MUTED)
        sub = sub[0] if sub else None
        d.text((xr - w / 2, y + 56 + (0 if sub or h <= 118 else (h - 118) / 2)), ar(value), font=F["val"],
               fill=color or TEXT, anchor="ma")
        if sub:
            ctext(d, xr - w / 2, y + 106, sub, F["small"], MUTED)
    return y + h


def check_icon(d, cx, cy, ok, color):
    r = 17
    if ok:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
        d.line([(cx - 8, cy), (cx - 2, cy + 7), (cx + 9, cy - 7)], fill=BG, width=5, joint="curve")
    else:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=LINE, width=4)


def checklist(d, y, checks, color):
    met = sum(ok for _, ok, _ in checks)
    rtext(d, W - PAD, y, "الشروط", F["h2"])
    d.text((PAD, y + 4), ar(f"تحقق {met} من {len(checks)}"), font=F["body"], fill=color, anchor="la")
    y += 66
    row = 54
    d.rounded_rectangle((PAD, y, W - PAD, y + row * len(checks) + 24), radius=24, fill=CARD)
    y += 12
    for i, (name, ok, detail) in enumerate(checks):
        cy = y + row / 2
        if i:
            d.line((PAD + 28, y, W - PAD - 28, y), fill=LINE, width=1)
        xr = W - PAD - 28
        check_icon(d, xr - 17, cy, ok, color)
        rtext(d, xr - 52, cy - 21, name, F["body"], TEXT if ok else MUTED)
        if detail:
            d.text((PAD + 28, cy - 19), ar(detail), font=F["small"], fill=color if ok else MUTED, anchor="la")
        y += row
    return y + 12


def footer(d, y, when):
    y += 28
    d.line((PAD, y, W - PAD, y), fill=LINE, width=2)
    y += 22
    rtext(d, W - PAD, y, f"{when} بتوقيت السعودية", F["small"], MUTED)
    d.text((PAD, y), ar("رادار السيولة"), font=F["small"], fill=MUTED, anchor="la")
    return y + 56


def finish(img, h):
    out = io.BytesIO()
    img.crop((0, 0, W, h)).save(out, "PNG", optimize=True)
    return out.getvalue()


def warning_box(d, y, title, reasons):
    """تحذير احتمال ارتداد السوق: عنوان وأسبابه، كل سبب بسطر."""
    h = 92 + 46 * len(reasons)
    d.rounded_rectangle((PAD, y, W - PAD, y + h), radius=24, fill=CARD, outline=SIGNAL, width=3)
    xr = W - PAD - 28
    d.ellipse((xr - 34, y + 26, xr, y + 60), fill=SIGNAL)
    ctext(d, xr - 17, y + 26, "!", F["h2"], BG)
    rtext(d, xr - 50, y + 22, title, F["h2"], SIGNAL)
    for i, r in enumerate(reasons):
        rtext(d, xr - 50, y + 84 + 46 * i, f"• {r}", F["body"], TEXT)
    return y + h + 28


def spx_card(sig, opt, when, cancel_time):
    """sig: side, score, checks. opt من rc.spx_option: نطاق الشراء، والوقف محسوب من طرفي النطاق. ما فيه هدف ربح."""
    up = sig["side"] == "up"
    color = UP if up else DN
    img = Image.new("RGB", (W, 3200), BG)
    d = ImageDraw.Draw(img)
    y = header(img, d, brand(img), "SPX", [("صعود" if up else "هبوط", color, BG)], sig["score"], len(sig["checks"]), color)

    lo, hi = round(opt["low"] * 100), round(opt["order"] * 100)
    top, h = y, 426
    d.rounded_rectangle((PAD, top, W - PAD, top + h), radius=28, fill=CARD, outline=color, width=3)
    xr = W - PAD - 28
    y = top + 30
    xr -= pill(d, xr, y + 14, opt["kind"].upper(), color, BG, F["h2"], 60) + 18
    d.text((xr, y), f"${opt['strike']:,.0f}", font=F["big"], fill=TEXT, anchor="ra")
    pill(d, PAD + 28 + tw(d, opt["exp_label"], F["pill"]) + 40, y + 14, opt["exp_label"], SIGNAL, BG, h=60)
    y += 108
    x0, x1 = PAD + 28, W - PAD - 28
    d.rounded_rectangle((x0, y, x1, y + 132), radius=18, fill=TILE, outline=SIGNAL, width=2)
    ctext(d, (x0 + x1) / 2, y + 12, "نطاق الشراء", F["small"], MUTED)
    d.text(((x0 + x1) / 2, y + 48), f"${lo:,}  -  ${hi:,}", font=F["big"], fill=SIGNAL, anchor="ma")
    y += 148
    stop = (f"الوقف -{STOP_PCT}%", f"${round(lo * (1 - STOP_PCT / 100)):,} - ${round(hi * (1 - STOP_PCT / 100)):,}", DN) \
        if opt["stop"] else ("الوقف", "بدون وقف", SIGNAL)
    tiles(d, y, [("سعر التنبيه", f"${opt['cost']:,.0f}", None), stop, ("ينلغي الأمر", cancel_time, None)])
    y = checklist(d, top + h + 28, sig["checks"], color)
    return finish(img, footer(d, y, when))


def spx_rise_card(kind, strike, exp_label, price, when):
    """تنبيه ارتفاع عقد SPX: السترايك والانتهاء وسعر العقد الحين فقط."""
    color = UP if kind == "call" else DN
    h = 520
    img = Image.new("RGB", (W, h), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((PAD, 40, W - PAD, h - 40), radius=28, fill=CARD, outline=color, width=3)
    xr, y = W - PAD - 32, 72
    xr -= pill(d, xr, y + 14, kind.upper(), color, BG, F["h2"], 60) + 18
    d.text((xr, y), f"${strike:,.0f}", font=F["big"], fill=TEXT, anchor="ra")
    pill(d, PAD + 32 + tw(d, exp_label, F["pill"]) + 40, y + 14, exp_label, SIGNAL, BG, h=60)
    ctext(d, W / 2, 190, "سعر العقد الآن", F["body"], MUTED)
    d.text((W / 2, 240), f"${price * 100:,.0f}", font=font("Bold", 128), fill=UP, anchor="ma")
    ctext(d, W / 2, 424, f"{when} · بيانات Alpaca", F["small"], MUTED)
    return finish(img, h)


def contract_card(sig, opt, when, trial=False, warning=None):
    """sig: sym, side, score, checks. opt: kind, strike, exp_label, today, bid, ask,
    cost, volume, oi. Greeks and spread drive the pick but stay off the card.
    trial = إشارة تجريبية (SPY)، warning = (عنوان، [أسباب]) لتحذير احتمال الارتداد."""
    up = sig["side"] == "up"
    color = UP if up else DN
    img = Image.new("RGB", (W, 3200), BG)
    d = ImageDraw.Draw(img)

    badges = [("صعود" if up else "هبوط", color, BG)]
    if trial:
        badges.append(("تجريبي · لا تتداول عليه", SIGNAL, BG))
    if sig.get("zone_text"):
        badges.append((sig["zone_text"], SIGNAL, BG))
    y = header(img, d, brand(img), sig["sym"], badges, sig["score"], len(sig["checks"]), color)

    # contract box
    top = y
    d.rounded_rectangle((PAD, top, W - PAD, top + 422), radius=28, fill=CARD, outline=color, width=3)
    xr = W - PAD - 28
    y = top + 30
    xr -= pill(d, xr, y + 14, opt["kind"].upper(), color, BG, F["h2"], 60) + 18
    d.text((xr, y), f"${opt['strike']:g}", font=F["big"], fill=TEXT, anchor="ra")
    if opt["today"]:
        pill(d, PAD + 28 + tw(d, opt["exp_label"], F["pill"]) + 40, y + 14, opt["exp_label"], SIGNAL, BG, h=60)
    else:
        w = tw(d, opt["exp_label"], F["pill"]) + 40
        d.rounded_rectangle((PAD + 28, y + 14, PAD + 28 + w, y + 74), radius=30, outline=MUTED, width=2)
        ctext(d, PAD + 28 + w / 2, y + 24, opt["exp_label"], F["pill"], TEXT)
    y += 112
    y = tiles(d, y, [("العرض", f"${opt['bid']:.2f}", None),
                     ("الطلب", f"${opt['ask']:.2f}", None),
                     ("تكلفة العقد", f"${opt['cost']:.0f}", SIGNAL)]) + 16
    y = tiles(d, y, [("التداول اليوم", f"{opt['volume']:,}", None),
                     ("العقود المفتوحة", f"{opt['oi']:,}", None)])
    y += 28 + 40
    if warning:
        y = warning_box(d, y - 12, *warning) + 12

    y = checklist(d, y, sig["checks"], color)
    return finish(img, footer(d, y, when))


LATE_PM, OFF_HIGH = 1.0, -5  # من اختبار 40 يوم: هنا تكثر الخسارة -10%، فالرقم يتلون أصفر


def penny_info(d, y, info):
    """البعد عن أعلى سعر اليوم + حجم ما قبل الافتتاح + خبر اليوم."""
    pm = info.get("pm")
    news = info.get("news") or []
    off = info.get("off_high", 0)
    y = tiles(d, y, [("من أعلى سعر اليوم", "عند القمة" if off > -0.5 else f"{off:.1f}%", SIGNAL if off <= OFF_HIGH else None),
                     ("حجم ما قبل الافتتاح", "—" if pm is None else f"{pm:.1f}x" if pm >= 0.1 else f"{pm:.2f}x",
                      SIGNAL if pm and pm >= LATE_PM else None, "من يوم عادي"),
                     ("خبر اليوم", "فيه" if news else "ما فيه", SIGNAL if news else MUTED)], h=150)
    if news:
        y += 20
        head = news[0] if len(news[0]) <= 58 else news[0][:56].rstrip() + "…"
        d.text((W - PAD - 28, y), head, font=F["small"], fill=MUTED, anchor="ra")
        y += 36
    return y + 40


def penny_card(sig, split, when, info=None):
    """sig: sym, price, chg, score, checks. split: None or ratio, date, drop.
    info: off_high (% عن أعلى سعر اليوم), pm (حجم ما قبل الافتتاح ÷ يوم عادي), news (عناوين اليوم)."""
    img = Image.new("RGB", (W, 3000), BG)
    d = ImageDraw.Draw(img)

    def price_row(y):
        x = W - PAD
        x -= rtext(d, x, y - 6, f"${sig['price']:.2f}", F["big"]) + 18
        pill(d, x, y + 10, f"{sig['chg']:+.2f}%", UP if sig["chg"] >= 0 else DN, BG)

    y = header(img, d, brand(img), sig["sym"],
               [("سهم صغير", SIGNAL, BG), ("صعود", UP, BG)],
               sig["score"], len(sig["checks"]), UP, sub=price_row)
    y += 96
    if info:
        y = penny_info(d, y, info)

    if split:
        d.rounded_rectangle((PAD, y, W - PAD, y + 132), radius=24, fill=CARD)
        d.rounded_rectangle((W - PAD - 10, y, W - PAD, y + 132), radius=5, fill=SIGNAL)
        rtext(d, W - PAD - 36, y + 18, f"تقسيم عكسي {split['ratio']}", F["h2"], SIGNAL)
        rtext(d, W - PAD - 36, y + 72, f"بتاريخ {split['date']}  ·  نزل بعده {split['drop']:.1f}%", F["body"], TEXT)
        y += 132 + 40

    y = checklist(d, y, sig["checks"], UP)
    return finish(img, footer(d, y, when))


def pct_tiles(d, y, items, x0=PAD + 28, x1=W - PAD - 28, h=150):
    """Tiles with a value and an optional colored percent under it: (label, value, pct)."""
    gap = 16
    n = len(items)
    w = (x1 - x0 - gap * (n - 1)) / n
    for i, (label, value, pct) in enumerate(items):
        xr = x1 - i * (w + gap)
        d.rounded_rectangle((xr - w, y, xr, y + h), radius=18, fill=TILE)
        ctext(d, xr - w / 2, y + 12, label, F["small"], MUTED)
        d.text((xr - w / 2, y + 50), value, font=F["val"], fill=TEXT, anchor="ma")
        if isinstance(pct, str):
            ctext(d, xr - w / 2, y + 100, pct, F["body"], MUTED)
        elif pct is not None:
            d.text((xr - w / 2, y + 100), ar(f"{pct:+.0f}%"), font=F["body"],
                   fill=UP if pct >= 0 else DN, anchor="ma")
    return y + h


def report_header(img, d, title, day, stats):
    """Logo + title + date, then summary tiles: stats = [(label, value, color)]."""
    y = PAD
    xr = W - PAD
    lw = logo(img, xr, y, 96)
    tx = xr - lw - (24 if lw else 0)
    rtext(d, tx, y - 4, title, F["big"])
    rtext(d, tx, y + 72, f"نهاية جلسة {day}", F["body"], MUTED)
    y += 150
    return tiles(d, y, stats, x0=PAD, x1=W - PAD, h=150) + 40


def money(x):
    return f"{'+' if x > 0 else '-' if x < 0 else ''}${abs(x):,.0f}"


def total_tile(rows, cost, one, many):
    """First summary tile: total cost of all the alerts and how many they were."""
    n = len(rows)
    return ("إجمالي الصفقات", f"${cost:,.0f}", None, f"{many if 3 <= n <= 10 else one} {n}")


def pnl_row(d, y, gain, loss, cost):
    """Total profit / total loss / net in dollars and as % of the total cost."""
    p = lambda x: x / cost * 100 if cost else 0
    pct_tiles(d, y, [("إجمالي الربح", money(gain), p(gain)),
                     ("إجمالي الخسارة", money(loss), p(loss)),
                     ("الصافي", money(gain + loss), p(gain + loss))], x0=PAD, x1=W - PAD)
    return y + 150 + 40


def pct(a, b):
    return (b / a - 1) * 100 if a else 0


WIN_PCT = 30   # win: the contract traded 30% above the alert price before hitting the stop (sold at the target)
STOP_PCT = 50  # stop: it fell 50% below the alert price before reaching the target (sold at the stop)


def contract_result(r):
    """win / stop / close. report.build works it out minute by minute; demos may pass it directly."""
    return r.get("result") or ("win" if r["high"] >= r["entry"] * (1 + WIN_PCT / 100) else "close")


def contract_pnl(r):
    """Dollars per contract under the rule above; contracts that hit neither count at the close."""
    cost = r["entry"] * 100
    return {"win": cost * WIN_PCT / 100, "stop": -cost * STOP_PCT / 100}.get(contract_result(r),
                                                                          (r["close"] - r["entry"]) * 100)


ROW_H = 108
ROWS_FIT = 1240  # height the table may take before rows shrink, so a busy day still fits one phone screen
_GEO = {}


def geo(n):
    """Row height and fonts for n rows: full size up to ~11 rows, then smaller down to 64px per row."""
    h = int(max(64, min(ROW_H, ROWS_FIT / max(1, n))))
    if h not in _GEO:
        k = h / ROW_H
        _GEO[h] = dict(h=h, cell=font("Bold", round(32 * max(k, 0.8))), tiny=font("Regular", round(23 * max(k, 0.82))),
                       pill=font("Bold", round(22 * max(k, 0.82))), ph=round(34 * max(k, 0.75)),
                       top=round(14 * k), val=round(22 * k), sub=round(64 * k), sub2=round(66 * k))
    return _GEO[h]


def table_height(n):
    return geo(n)["h"] * max(1, n)
COL_W = 196  # three value columns on the left; the contract/stock name takes the rest


def col_x(i):
    """Center of value column i (0 = alert price, 1 = high, 2 = close), right to left."""
    return PAD + 24 + COL_W * (2 - i) + COL_W / 2


def table(d, y, rows, name_label, draw_name, tag=lambda r: None, entry_label="سعر التنبيه"):
    """One compact row per alert so the whole report fits one phone-sized image."""
    heads = (name_label, entry_label, "أعلى سعر", "الإغلاق")
    rtext(d, W - PAD - 28, y, heads[0], F["small"], MUTED)
    for i, h in enumerate(heads[1:]):
        ctext(d, col_x(i), y, h, F["small"], MUTED)
    y += 46
    top = y
    g = geo(len(rows))
    rh = g["h"]
    d.rounded_rectangle((PAD, y, W - PAD, y + rh * len(rows) + 8), radius=24, fill=CARD)
    y += 4
    for k, r in enumerate(rows):
        if k:
            d.line((PAD + 24, y, W - PAD - 24, y), fill=LINE, width=1)
        color = draw_name(d, y, r, g)
        d.rounded_rectangle((W - PAD - 8, y + rh // 6, W - PAD, y + rh - rh // 6), radius=4, fill=color)
        d.text((col_x(0), y + g["val"]), f"${r['entry']:.2f}", font=g["cell"], fill=TEXT, anchor="ma")
        for i, val, extra in ((1, r["high"], r["high_time"]), (2, r["close"], None)):
            d.text((col_x(i), y + g["val"]), f"${val:.2f}", font=g["cell"], fill=TEXT, anchor="ma")
            p = pct(r["entry"], val)
            line = f"{p:+.0f}%" + (f"  {extra}" if extra else "")
            if i == 2 and tag(r):
                line, p = tag(r)
            d.text((col_x(i), y + g["sub2"]), ar(line), font=g["tiny"], fill=UP if p >= 0 else DN, anchor="ma")
        y += rh
    return top + rh * len(rows) + 8 + 24


def notes_block(d, y, notes):
    for line in notes:
        rtext(d, W - PAD, y, line, geo(1)["tiny"], MUTED)
        y += 36
    return y - 20


def contract_report(rows, day, when, title="تقرير العقود", target=True):
    """rows: sym, side, kind, strike, exp_label, time, entry, high, high_time, close.
    entry = ask at the alert; high/close come from the contract's trades after it."""
    img = Image.new("RGB", (W, 760 + 60 + table_height(len(rows)) + 200), BG)
    d = ImageDraw.Draw(img)
    good = (lambda r: contract_result(r) == "win") if target else (lambda r: contract_pnl(r) > 0)
    win = sum(good(r) for r in rows)
    cost = sum(r["entry"] * 100 for r in rows)
    y = report_header(img, d, title, day, [
        total_tile(rows, cost, "عقد", "عقود"),
        ("رابحة", str(win), UP),
        ("خاسرة", str(len(rows) - win), DN)])
    if not rows:
        d.rounded_rectangle((PAD, y, W - PAD, y + 140), radius=24, fill=CARD)
        ctext(d, W / 2, y + 48, "ما جاك ولا تنبيه عقد اليوم", F["h2"], MUTED)
        return finish(img, footer(d, y + 140, when))
    # winners count +WIN_PCT; every contract that missed the target goes to the loss side (stop or close)
    y = pnl_row(d, y - 24, sum(contract_pnl(r) for r in rows if good(r)),
                sum(contract_pnl(r) for r in rows if not good(r)), cost) - 6

    def name(d, y, r, g):
        color = UP if r["side"] == "up" else DN
        xr = W - PAD - 28
        xr -= rtext(d, xr, y + g["top"], r["sym"], g["cell"]) + 12
        xr -= pill(d, xr, y + g["top"] + 6, r["kind"].upper(), color, BG, f=g["pill"], h=g["ph"]) + 10
        rtext(d, xr, y + g["top"], f"${r['strike']:g}", g["cell"])
        rtext(d, W - PAD - 28, y + g["sub"], f"{r['time']} · {r['exp_label']}", g["tiny"], MUTED)
        return color

    tags = {"win": (f"هدف +{WIN_PCT}%", 1), "stop": ("ضرب الوقف", -1)}
    y = table(d, y, rows, "العقد", name, tag=lambda r: tags.get(contract_result(r)),
              entry_label="سعر التنبيه" if target else "سعر الأمر")
    y = notes_block(d, y, [
        f"رابحة = وصل العقد +{WIN_PCT}% قبل ما ينزل {STOP_PCT}%، وتنحسب ربح {WIN_PCT}% على أساس البيع عند الهدف.",
        f"ضرب الوقف = نزل {STOP_PCT}% قبل الهدف، وتنحسب خسارة {STOP_PCT}%. غير كذا تنحسب على سعر الإغلاق."] if target else [
        "ما فيه هدف ربح: كل عقد ينحسب من سعر أمر الشراء إلى سعر الإغلاق.",
        f"ضرب الوقف = نزل {STOP_PCT}% من سعر الأمر، وتنحسب خسارة {STOP_PCT}%. الساعة الأخيرة بدون وقف."])
    return finish(img, footer(d, y, when))


PENNY_STAKE = 1000  # dollars assumed per penny alert for the report totals


def penny_report(rows, day, when):
    """rows: sym, score, time, entry, high, high_time, close (stock prices)."""
    img = Image.new("RGB", (W, 760 + 60 + table_height(len(rows)) + 200), BG)
    d = ImageDraw.Draw(img)
    win = sum(r["close"] > r["entry"] for r in rows)
    y = report_header(img, d, "تقرير الأسهم الصغيرة", day, [
        total_tile(rows, PENNY_STAKE * len(rows), "سهم", "أسهم"),
        ("أغلق فوق سعر التنبيه", str(win), UP),
        ("أغلق تحت سعر التنبيه", str(len(rows) - win), DN)])
    if not rows:
        d.rounded_rectangle((PAD, y, W - PAD, y + 140), radius=24, fill=CARD)
        ctext(d, W / 2, y + 48, "ما جاك ولا تنبيه سهم صغير اليوم", F["h2"], MUTED)
        return finish(img, footer(d, y + 140, when))
    res = [PENNY_STAKE * pct(r["entry"], r["close"]) / 100 for r in rows]
    y = pnl_row(d, y - 24, sum(x for x in res if x > 0), sum(x for x in res if x <= 0),
                PENNY_STAKE * len(rows)) - 6

    def name(d, y, r, g):
        xr = W - PAD - 28
        xr -= rtext(d, xr, y + g["top"], r["sym"], g["cell"]) + 12
        pill(d, xr, y + g["top"] + 6, f"{r['score']}/10", SIGNAL, BG, f=g["pill"], h=g["ph"])
        rtext(d, W - PAD - 28, y + g["sub"], f"التنبيه {r['time']}", g["tiny"], MUTED)
        return SIGNAL

    y = table(d, y, rows, "السهم", name)
    y = notes_block(d, y, [f"الإجمالي على افتراض {PENNY_STAKE:,}$ لكل تنبيه، من سعر التنبيه إلى الإغلاق."])
    return finish(img, footer(d, y, when))


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    up_checks = [
        ("سيولة داخلة قوية", True, "72%"),
        ("اختراق متوسط 8 أيام", True, "اخترق اليوم"),
        ("حجم غير معتاد", True, "2.4x"),
        ("فوق VWAP", True, ""),
        ("زخم صاعد", True, "+0.85%"),
        ("حركة أكبر من الطبيعي", True, "1.2x"),
        ("أقوى من السوق", True, "+1.6%"),
        ("السوق داعم", True, "SPY +0.5%"),
        ("اختراق ترند هابط 15د", False, "باقي 0.4%"),
        ("اختراق ترند هابط ساعة", False, "باقي 1.1%"),
    ]
    sig = dict(sym="TSLA", side="up", score=8, checks=up_checks, zone_text="اختراق منطقة بيع")
    opt = dict(kind="call", strike=415, exp_label="ينتهي اليوم", today=True, bid=1.48, ask=1.52,
               spread=2.6, cost=150, volume=4820, oi=12350, delta=0.42, gamma=0.085, theta=-0.31)
    open(os.path.join(out, "card-contract.png"), "wb").write(contract_card(sig, opt, "10:45 م"))

    penny_checks = [
        ("سيولة داخلة قوية", True, "78%"),
        ("اختراق متوسط 20 يوم", True, "اخترق اليوم"),
        ("حجم غير معتاد", True, "5.1x"),
        ("فوق VWAP", True, ""),
        ("زخم صاعد", True, "+2.10%"),
        ("حركة أكبر من الطبيعي", True, "1.8x"),
        ("أقوى من السوق", True, "+5.9%"),
        ("السوق داعم", False, "SPY -0.2%"),
        ("اختراق ترند هابط 15د", False, "باقي 0.9%"),
        ("اختراق ترند هابط ساعة", False, "باقي 2.3%"),
    ]
    psig = dict(sym="ABCD", price=2.84, chg=6.40, score=7, checks=penny_checks)
    split = dict(ratio="1:10", date="10/24", drop=14.2)
    open(os.path.join(out, "card-penny.png"), "wb").write(penny_card(psig, split, "11:20 م"))

    day = "الإثنين 11/02"
    crow = [dict(sym="TSLA", side="up", kind="call", strike=415, exp_label="ينتهي اليوم", time="5:12 م",
                 entry=1.52, high=2.40, high_time="6:05 م", close=0.40, result="win"),
            dict(sym="NVDA", side="down", kind="put", strike=198, exp_label="ينتهي الجمعة 11/06", time="7:40 م",
                 entry=1.85, high=2.35, high_time="8:15 م", close=2.10),
            dict(sym="META", side="up", kind="call", strike=640, exp_label="ينتهي الجمعة 11/06", time="9:02 م",
                 entry=1.20, high=1.26, high_time="9:10 م", close=0.30, result="stop"),
            dict(sym="AMD", side="up", kind="call", strike=262.5, exp_label="ينتهي اليوم", time="9:30 م",
                 entry=1.60, high=1.95, high_time="10:12 م", close=1.75)]
    crow += [dict(sym="AAPL", side="down", kind="put", strike=265, exp_label="ينتهي اليوم", time="10:05 م",
                  entry=1.45, high=2.10, high_time="10:40 م", close=1.90),
             dict(sym="MSFT", side="up", kind="call", strike=520, exp_label="ينتهي الجمعة 11/06", time="10:20 م",
                  entry=1.90, high=2.05, high_time="10:31 م", close=1.30),
             dict(sym="GOOGL", side="up", kind="call", strike=285, exp_label="ينتهي اليوم", time="10:48 م",
                  entry=1.10, high=1.75, high_time="11:20 م", close=1.55),
             dict(sym="AVGO", side="down", kind="put", strike=345, exp_label="ينتهي الجمعة 11/06", time="11:15 م",
                  entry=1.70, high=1.82, high_time="11:22 م", close=0.55, result="stop"),
             dict(sym="NFLX", side="up", kind="call", strike=1120, exp_label="ينتهي الجمعة 11/06", time="11:40 م",
                  entry=1.95, high=2.60, high_time="11:58 م", close=2.45)]
    open(os.path.join(out, "report-contracts.png"), "wb").write(contract_report(crow, day, "11:05 م"))
    prow = [dict(sym="ABCD", score=7, time="6:20 م", entry=2.84, high=3.31, high_time="7:02 م", close=3.05),
            dict(sym="WXYZ", score=8, time="8:45 م", entry=1.62, high=1.70, high_time="8:52 م", close=1.41)]
    prow = [dict(prow[i % 2], sym=f"S{i:02d}") for i in range(17)]
    open(os.path.join(out, "report-penny.png"), "wb").write(penny_report(prow, day, "11:05 م"))
