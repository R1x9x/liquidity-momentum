"""
يضبط بوت الأسهم الصغيرة بدون لصق في الـ Console:
ترسل توكن البوت الجديد كرسالة للبوت الأساسي في تليجرام، وهالسكربت يقراه من هناك،
يتأكد إنه شغال، ويحفظه في /opt/radar/.env باسم PENNY_TG_TOKEN، ويحاول يحذف رسالة التوكن.
تشغيل:  bash /opt/radar/repo/server/penny-bot.sh
"""
import os
import re
import sys

import requests

ENV = os.environ.get("RADAR_ENV", "/opt/radar/.env")
TOKEN = os.environ["TG_TOKEN"]
CHAT = str(os.environ["TG_CHAT_ID"])
API = "https://api.telegram.org/bot{}/{}"
PATTERN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}")


def call(token, method, **params):
    try:
        return requests.post(API.format(token, method), data=params, timeout=20).json()
    except Exception:
        return {}


def find_token():
    """أحدث رسالة منك للبوت الأساسي فيها توكن صالح (غير توكن البوت الأساسي)."""
    upd = call(TOKEN, "getUpdates", limit=100).get("result", [])
    for u in reversed(upd):
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != CHAT:
            continue
        for tok in PATTERN.findall(m.get("text", "")):
            if tok == TOKEN:
                continue
            me = call(tok, "getMe")
            if me.get("ok"):
                return tok, me["result"].get("username", ""), m["message_id"]
    return None, None, None


def save(tok):
    lines = []
    if os.path.exists(ENV):
        lines = [l for l in open(ENV).read().splitlines() if not l.startswith("PENNY_TG_TOKEN=")]
    lines.append(f"PENNY_TG_TOKEN={tok}")
    with open(ENV, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(ENV, 0o600)


def main():
    tok, name, msg_id = find_token()
    if not tok:
        print("ما لقيت توكن. أرسل توكن البوت الجديد كرسالة للبوت الأساسي (بوت العقود) وجرّب مرة ثانية.")
        sys.exit(1)
    save(tok)
    deleted = call(TOKEN, "deleteMessage", chat_id=CHAT, message_id=msg_id).get("ok")
    call(TOKEN, "sendMessage", chat_id=CHAT,
         text=f"✅ انحفظ توكن بوت الأسهم (@{name})."
              + ("" if deleted else "\nاحذف رسالة التوكن من هالمحادثة بيدك."))
    print(f"تم: بوت الأسهم @{name}" + ("" if deleted else " — احذف رسالة التوكن من تليجرام بيدك"))


if __name__ == "__main__":
    main()
