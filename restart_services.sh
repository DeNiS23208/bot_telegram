#!/bin/bash
# Скрипт для перезапуска бота и webhook

REPO_DIR="/opt/bot_telegram"
cd "$REPO_DIR"

echo "🗑️ Очистка базы данных..."
python3 clear_db.py --full --yes

echo ""
echo "Поиск запущенных процессов..."

# Ищем процесс бота
BOT_PID=$(ps aux | grep "[p]ython.*bot.py" | awk '{print $2}')
if [ ! -z "$BOT_PID" ]; then
    echo "Найден процесс бота (PID: $BOT_PID), останавливаем..."
    kill $BOT_PID
    sleep 2
fi

# Ищем процесс webhook
WEBHOOK_PID=$(ps aux | grep "[u]vicorn.*webhook_app" | awk '{print $2}')
if [ ! -z "$WEBHOOK_PID" ]; then
    echo "Найден процесс webhook (PID: $WEBHOOK_PID), останавливаем..."
    kill $WEBHOOK_PID
    sleep 2
fi

echo ""
echo "Проверяем systemd сервисы..."

# Проверяем какие сервисы есть
systemctl list-units --type=service | grep -E "(bot|webhook|telegram)" || echo "Сервисы не найдены"

echo ""
echo "Если сервисы не найдены, запустите вручную:"
echo "1. Для бота: cd /opt/bot_telegram && source venv/bin/activate && python3 bot.py &"
echo "2. Для webhook: cd /opt/bot_telegram && source venv/bin/activate && uvicorn webhook_app:app --host 0.0.0.0 --port 8000 &"



