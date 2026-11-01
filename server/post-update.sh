#!/usr/bin/env bash
# يشتغل بعد git pull من أمر radar-update: يثبت المكتبات، ويشغّل التنبيهات من ملف المستودع، ويعيد تشغيل الخدمات
set -e
BASE=/opt/radar
$BASE/venv/bin/pip install -q -r $BASE/repo/requirements.txt
UNIT=/etc/systemd/system/radar-alerts.service
if ! grep -q "repo/server/alerts.py" "$UNIT"; then
  sed -i "s#^ExecStart=.*#ExecStart=$BASE/venv/bin/python $BASE/repo/server/alerts.py#" "$UNIT"
  systemctl daemon-reload
fi
systemctl restart radar-web radar-alerts
systemctl restart radar-public 2>/dev/null || true
echo "تم التحديث ✅"
