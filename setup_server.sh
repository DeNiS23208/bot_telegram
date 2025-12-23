#!/bin/bash
# Скрипт для первоначальной настройки бота на сервере
# Использование: ./setup_server.sh

set -e  # Остановка при ошибке

echo "🚀 Начало настройки Telegram бота на сервере..."

# Проверка прав root
if [ "$EUID" -ne 0 ]; then 
    echo "❌ Пожалуйста, запустите скрипт от root или с sudo"
    exit 1
fi

# Обновление системы
echo "📦 Обновление системы..."
apt update && apt upgrade -y

# Установка необходимых пакетов
echo "📦 Установка Python и зависимостей..."
apt install -y python3 python3-pip python3-venv git curl

# Создание директории
echo "📁 Создание директории /opt/bot_telegram..."
mkdir -p /opt/bot_telegram
cd /opt/bot_telegram

# Создание виртуального окружения
echo "🐍 Создание виртуального окружения..."
python3 -m venv venv
source venv/bin/activate

# Обновление pip
echo "📦 Обновление pip..."
pip install --upgrade pip

# Установка зависимостей
if [ -f "requirements.txt" ]; then
    echo "📦 Установка зависимостей из requirements.txt..."
    pip install -r requirements.txt
else
    echo "⚠️ Файл requirements.txt не найден. Установка базовых зависимостей..."
    pip install aiogram==3.23.0 aiosqlite==0.22.0 python-dotenv==1.0.1 yookassa==3.9.0 pytz==2024.1 fastapi==0.124.4 uvicorn==0.38.0
fi

# Проверка наличия .env
if [ ! -f ".env" ]; then
    echo "⚠️ Файл .env не найден!"
    echo "📝 Создайте файл .env с необходимыми переменными окружения."
    echo "Пример содержимого:"
    echo ""
    echo "BOT_TOKEN=your_telegram_bot_token"
    echo "BOT_USERNAME=your_bot_username"
    echo "CHANNEL_ID=your_channel_id"
    echo "YOOKASSA_SHOP_ID=your_shop_id"
    echo "YOOKASSA_SECRET_KEY=your_secret_key"
    echo "YOOKASSA_RETURN_URL=https://your-domain.com/payment/return"
    echo "DB_PATH=/opt/bot_telegram/bot.db"
    echo "PAYMENT_CUSTOMER_EMAIL=your-email@example.com"
    echo ""
    echo "Создайте файл: nano /opt/bot_telegram/.env"
else
    echo "✅ Файл .env найден"
    chmod 600 .env
fi

# Установка systemd сервисов
if [ -f "telegram-bot.service" ] && [ -f "webhook.service" ]; then
    echo "⚙️ Установка systemd сервисов..."
    cp telegram-bot.service /etc/systemd/system/
    cp webhook.service /etc/systemd/system/
    systemctl daemon-reload
    
    echo "✅ Сервисы установлены"
    echo ""
    echo "📋 Следующие шаги:"
    echo "1. Настройте файл .env с правильными значениями"
    echo "2. Включите автозапуск: systemctl enable telegram-bot webhook"
    echo "3. Запустите сервисы: systemctl start telegram-bot webhook"
    echo "4. Проверьте статус: systemctl status telegram-bot webhook"
else
    echo "⚠️ Файлы сервисов не найдены (telegram-bot.service, webhook.service)"
fi

# Установка прав доступа
echo "🔒 Настройка прав доступа..."
chown -R root:root /opt/bot_telegram
chmod 700 /opt/bot_telegram

echo ""
echo "✅ Настройка завершена!"
echo ""
echo "📚 Дополнительная информация в файле DEPLOYMENT.md"

