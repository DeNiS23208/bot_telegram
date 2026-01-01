#!/bin/bash
# Скрипт для перезапуска бота и webhook

REPO_DIR="/opt/bot_telegram"
cd "$REPO_DIR"

echo "🗑️ Очистка базы данных..."
python3 clear_db.py --full --yes

echo ""
echo "🔄 Сброс бонусной недели..."
python3 reset_bonus_week.py

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
echo "🔄 Перезапуск systemd сервисов..."

# Перезапускаем systemd сервисы
systemctl restart telegram-bot webhook

sleep 3

echo ""
echo "✅ Проверка статуса сервисов..."
systemctl status telegram-bot webhook --no-pager | head -20

echo ""
echo "✅ Готово! База данных очищена, сервисы перезапущены."
