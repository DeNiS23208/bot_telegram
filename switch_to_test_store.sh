#!/bin/bash
# Скрипт для переключения на тестовый магазин ЮKassa

echo "🔄 Переключение на тестовый магазин ЮKassa"
echo ""
echo "⚠️  ВАЖНО: Убедитесь, что у вас есть тестовые credentials из личного кабинета ЮKassa"
echo ""
read -p "Введите тестовый Shop ID: " TEST_SHOP_ID
read -p "Введите тестовый Secret Key: " TEST_SECRET_KEY

if [ -z "$TEST_SHOP_ID" ] || [ -z "$TEST_SECRET_KEY" ]; then
    echo "❌ Ошибка: Shop ID и Secret Key не могут быть пустыми"
    exit 1
fi

echo ""
echo "📝 Обновление .env файла на сервере..."
ssh -o StrictHostKeyChecking=no root@178.72.153.64 << EOF
cd /opt/bot_telegram

# Создаем резервную копию
cp .env .env.backup.\$(date +%Y%m%d_%H%M%S)

# Обновляем значения
sed -i "s/YOOKASSA_SHOP_ID=.*/YOOKASSA_SHOP_ID=$TEST_SHOP_ID/" .env
sed -i "s/YOOKASSA_SECRET_KEY=.*/YOOKASSA_SECRET_KEY=$TEST_SECRET_KEY/" .env

echo "✅ .env файл обновлен"
echo ""
echo "Текущие значения:"
grep YOOKASSA .env
EOF

echo ""
echo "🔄 Перезапуск сервисов..."
ssh -o StrictHostKeyChecking=no root@178.72.153.64 "systemctl restart telegram-bot webhook && sleep 2 && systemctl status telegram-bot webhook --no-pager -l | head -20"

echo ""
echo "✅ Переключение завершено!"
echo ""
echo "📋 Проверьте логи на наличие ошибок:"
echo "   ssh root@178.72.153.64 'journalctl -u telegram-bot -u webhook --since \"1 minute ago\" -n 30'"

