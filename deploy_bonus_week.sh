#!/bin/bash

echo "🚀 Загрузка файлов на сервер..."

scp -o StrictHostKeyChecking=no config.py bot.py webhook_app.py root@178.72.153.64:/opt/bot_telegram/

echo "✅ Файлы загружены"

echo "🔄 Перезапуск сервисов..."

ssh -o StrictHostKeyChecking=no root@178.72.153.64 << 'SSHEOF'
cd /opt/bot_telegram
echo "yes" | python3 clear_db.py --full
systemctl restart telegram-bot webhook
sleep 3
systemctl status telegram-bot webhook --no-pager | head -20
SSHEOF

echo "✅ Готово!"

