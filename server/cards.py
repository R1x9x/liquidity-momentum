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

BG = "#18202D"
CARD = "#222C3C"
TILE = "#2A3547"
LINE = "#33405A"
TEXT = "#E6EAF0"
MUTED = "#8A96A8"
UP = "#3CC7A6"
DN = "#EE6B5E"
SIGNAL = "#F2B544"

W = 1080
PAD = 48


def font(weight, size):
    return ImageFont.truetype(os.path.join(FONTS, f"IBMPlexSansArabic-{weight}.ttf"), size)


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
    for i, (label, value, color) in enumerate(items):
        xr = x1 - i * (w + gap)
        d.rounded_rectangle((xr - w, y, xr, y + h), radius=18, fill=TILE)
        ctext(d, xr - w / 2, y + 14, label, F["small"], MUTED)
        d.text((xr - w / 2, y + 56), value, font=F["val"], fill=color or TEXT, anchor="ma")
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
    row = 58
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


def contract_card(sig, opt, when):
    """sig: sym, side, score, checks. opt: kind, strike, exp_label, today, bid, ask,
    cost, volume, oi. Greeks and spread drive the pick but stay off the card."""
    up = sig["side"] == "up"
    color = UP if up else DN
    img = Image.new("RGB", (W, 2400), BG)
    d = ImageDraw.Draw(img)

    y = header(img, d, PAD, sig["sym"],
               [("صعود" if up else "هبوط", color, BG)],
               sig["score"], len(sig["checks"]), color)

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

    y = checklist(d, y, sig["checks"], color)
    return finish(img, footer(d, y, when))


def penny_card(sig, split, when):
    """sig: sym, price, chg, score, checks. split: None or ratio, date, drop."""
    img = Image.new("RGB", (W, 2400), BG)
    d = ImageDraw.Draw(img)

    def price_row(y):
        x = W - PAD
        x -= rtext(d, x, y - 6, f"${sig['price']:.2f}", F["big"]) + 18
        pill(d, x, y + 10, f"{sig['chg']:+.2f}%", UP if sig["chg"] >= 0 else DN, BG)

    y = header(img, d, PAD, sig["sym"],
               [("سهم صغير", SIGNAL, BG), ("صعود", UP, BG)],
               sig["score"], len(sig["checks"]), UP, sub=price_row)
    y += 96

    if split:
        d.rounded_rectangle((PAD, y, W - PAD, y + 132), radius=24, fill=CARD)
        d.rounded_rectangle((W - PAD - 10, y, W - PAD, y + 132), radius=5, fill=SIGNAL)
        rtext(d, W - PAD - 36, y + 18, f"تقسيم عكسي {split['ratio']}", F["h2"], SIGNAL)
        rtext(d, W - PAD - 36, y + 72, f"بتاريخ {split['date']}  ·  نزل بعده {split['drop']:.1f}%", F["body"], TEXT)
        y += 132 + 40

    y = checklist(d, y, sig["checks"], UP)
    return finish(img, footer(d, y, when))


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    up_checks = [
        ("سيولة داخلة قوية", True, "72%"),
        ("اختراق متوسط 20 يوم", True, "اخترق اليوم"),
        ("حجم غير معتاد", True, "2.4x"),
        ("فوق VWAP", True, ""),
        ("زخم صاعد", True, "+0.85%"),
        ("حركة أكبر من الطبيعي", True, "1.2x"),
        ("أقوى من السوق", True, "+1.6%"),
        ("السوق داعم", True, "SPY +0.5%"),
        ("اختراق ترند هابط 15د", False, "باقي 0.4%"),
        ("اختراق ترند هابط ساعة", False, "باقي 1.1%"),
    ]
    sig = dict(sym="TSLA", side="up", score=8, checks=up_checks)
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
