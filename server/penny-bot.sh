#!/bin/bash
# يضبط بوت الأسهم الصغيرة من توكن مرسل للبوت الأساسي، وبعدها يعيد تشغيل التنبيهات
set -a; . /opt/radar/.env; set +a
/opt/radar/venv/bin/python /opt/radar/repo/server/set_penny_bot.py && systemctl restart radar-alerts
