#!/bin/bash

echo "🔍 Проверка файлов на сервере..."

ssh -o StrictHostKeyChecking=no root@178.72.153.64 << 'SSHEOF'
cd /opt/bot_telegram

echo "=== Проверка config.py ==="
grep -n "dni_prazdnika\|is_bonus_week_active" config.py | head -5

echo ""
echo "=== Проверка bot.py ==="
grep -n "BTN_BONUS_WEEK\|bonus_week_menu\|is_bonus_week_active" bot.py | head -5

echo ""
echo "=== Проверка статуса бонусной недели ==="
python3 << 'PYEOF'
import sys
sys.path.insert(0, '/opt/bot_telegram')
try:
    from config import is_bonus_week_active, dni_prazdnika, vremya_sms
    print(f"dni_prazdnika: {dni_prazdnika}")
    print(f"vremya_sms: {vremya_sms}")
    print(f"Бонусная неделя активна: {is_bonus_week_active()}")
except Exception as e:
    print(f"Ошибка: {e}")
PYEOF

echo ""
echo "=== Статус сервисов ==="
systemctl is-active telegram-bot webhook
SSHEOF

