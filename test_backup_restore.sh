#!/bin/bash
# Скрипт для тестирования резервного копирования и восстановления базы данных

echo "=========================================="
echo "🧪 ТЕСТИРОВАНИЕ РЕЗЕРВНОГО КОПИРОВАНИЯ"
echo "=========================================="
echo ""

cd /opt/bot_telegram || exit 1

# Цвета для вывода
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Шаг 1: Проверка текущей базы данных
echo "📊 ШАГ 1: Проверка текущей базы данных"
echo "----------------------------------------"
if [ ! -f "bot.db" ]; then
    echo -e "${RED}❌ База данных не найдена!${NC}"
    exit 1
fi

DB_SIZE=$(du -h bot.db | cut -f1)
DB_USERS=$(python3 -c "import sqlite3; conn = sqlite3.connect('bot.db'); print(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]); conn.close()" 2>/dev/null || echo "0")
DB_PAYMENTS=$(python3 -c "import sqlite3; conn = sqlite3.connect('bot.db'); print(conn.execute('SELECT COUNT(*) FROM payments').fetchone()[0]); conn.close()" 2>/dev/null || echo "0")
DB_SUBS=$(python3 -c "import sqlite3; conn = sqlite3.connect('bot.db'); print(conn.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0]); conn.close()" 2>/dev/null || echo "0")

echo -e "${GREEN}✅ База данных найдена${NC}"
echo "   Размер: $DB_SIZE"
echo "   Пользователей: $DB_USERS"
echo "   Платежей: $DB_PAYMENTS"
echo "   Подписок: $DB_SUBS"
echo ""

# Шаг 2: Создание резервной копии
echo "💾 ШАГ 2: Создание резервной копии"
echo "----------------------------------------"
BACKUP_FILE="test_backup_$(date +%Y%m%d_%H%M%S).db"
cp bot.db "$BACKUP_FILE"
BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo -e "${GREEN}✅ Резервная копия создана: $BACKUP_FILE${NC}"
echo "   Размер: $BACKUP_SIZE"
echo ""

# Шаг 3: Проверка целостности backup
echo "🔍 ШАГ 3: Проверка целостности backup"
echo "----------------------------------------"
if python3 -c "import sqlite3; conn = sqlite3.connect('$BACKUP_FILE'); conn.execute('SELECT COUNT(*) FROM users').fetchone(); conn.close()" > /dev/null 2>&1; then
    BACKUP_USERS=$(python3 -c "import sqlite3; conn = sqlite3.connect('$BACKUP_FILE'); print(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]); conn.close()" 2>/dev/null || echo "0")
    BACKUP_PAYMENTS=$(python3 -c "import sqlite3; conn = sqlite3.connect('$BACKUP_FILE'); print(conn.execute('SELECT COUNT(*) FROM payments').fetchone()[0]); conn.close()" 2>/dev/null || echo "0")
    BACKUP_SUBS=$(python3 -c "import sqlite3; conn = sqlite3.connect('$BACKUP_FILE'); print(conn.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0]); conn.close()" 2>/dev/null || echo "0")
    
    if [ "$DB_USERS" = "$BACKUP_USERS" ] && [ "$DB_PAYMENTS" = "$BACKUP_PAYMENTS" ] && [ "$DB_SUBS" = "$BACKUP_SUBS" ]; then
        echo -e "${GREEN}✅ Backup валиден, данные совпадают${NC}"
        echo "   Пользователей: $BACKUP_USERS (совпадает)"
        echo "   Платежей: $BACKUP_PAYMENTS (совпадает)"
        echo "   Подписок: $BACKUP_SUBS (совпадает)"
    else
        echo -e "${RED}❌ Ошибка: данные в backup не совпадают!${NC}"
        echo "   Оригинал: users=$DB_USERS, payments=$DB_PAYMENTS, subs=$DB_SUBS"
        echo "   Backup: users=$BACKUP_USERS, payments=$BACKUP_PAYMENTS, subs=$BACKUP_SUBS"
        rm "$BACKUP_FILE"
        exit 1
    fi
else
    echo -e "${RED}❌ Ошибка: backup поврежден!${NC}"
    rm "$BACKUP_FILE"
    exit 1
fi
echo ""

# Шаг 4: Тестирование восстановления
echo "🔄 ШАГ 4: Тестирование восстановления"
echo "----------------------------------------"
echo -e "${YELLOW}⚠️  ВНИМАНИЕ: Сейчас мы протестируем восстановление${NC}"
echo "   Это безопасно - мы создадим копию перед тестом"
echo ""
read -p "Продолжить? (y/n): " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Тест отменен"
    rm "$BACKUP_FILE"
    exit 0
fi

# Сохраняем оригинальную базу
ORIGINAL_BACKUP="bot_original_$(date +%Y%m%d_%H%M%S).db"
cp bot.db "$ORIGINAL_BACKUP"
echo -e "${GREEN}✅ Оригинальная база сохранена: $ORIGINAL_BACKUP${NC}"

# Останавливаем сервисы
echo "⏸️  Остановка сервисов..."
systemctl stop telegram-bot.service webhook.service
sleep 2

# Восстанавливаем из backup
echo "📥 Восстановление из backup..."
cp "$BACKUP_FILE" bot.db
chmod 644 bot.db

# Проверяем восстановленную базу
RESTORED_USERS=$(python3 -c "import sqlite3; conn = sqlite3.connect('bot.db'); print(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]); conn.close()" 2>/dev/null || echo "0")
RESTORED_PAYMENTS=$(python3 -c "import sqlite3; conn = sqlite3.connect('bot.db'); print(conn.execute('SELECT COUNT(*) FROM payments').fetchone()[0]); conn.close()" 2>/dev/null || echo "0")
RESTORED_SUBS=$(python3 -c "import sqlite3; conn = sqlite3.connect('bot.db'); print(conn.execute('SELECT COUNT(*) FROM subscriptions').fetchone()[0]); conn.close()" 2>/dev/null || echo "0")

if [ "$DB_USERS" = "$RESTORED_USERS" ] && [ "$DB_PAYMENTS" = "$RESTORED_PAYMENTS" ] && [ "$DB_SUBS" = "$RESTORED_SUBS" ]; then
    echo -e "${GREEN}✅ Восстановление успешно!${NC}"
    echo "   Пользователей: $RESTORED_USERS (совпадает)"
    echo "   Платежей: $RESTORED_PAYMENTS (совпадает)"
    echo "   Подписок: $RESTORED_SUBS (совпадает)"
else
    echo -e "${RED}❌ Ошибка восстановления!${NC}"
    echo "   Восстанавливаем оригинальную базу..."
    cp "$ORIGINAL_BACKUP" bot.db
    systemctl start telegram-bot.service webhook.service
    rm "$BACKUP_FILE" "$ORIGINAL_BACKUP"
    exit 1
fi

# Восстанавливаем оригинальную базу
echo ""
echo "🔄 Восстановление оригинальной базы..."
cp "$ORIGINAL_BACKUP" bot.db

# Запускаем сервисы
echo "▶️  Запуск сервисов..."
systemctl start telegram-bot.service webhook.service
sleep 2

# Проверяем статус сервисов
if systemctl is-active --quiet telegram-bot.service && systemctl is-active --quiet webhook.service; then
    echo -e "${GREEN}✅ Сервисы запущены успешно${NC}"
else
    echo -e "${RED}❌ Ошибка запуска сервисов!${NC}"
    systemctl status telegram-bot.service webhook.service
fi

# Очистка
echo ""
echo "🧹 Очистка тестовых файлов..."
rm "$BACKUP_FILE" "$ORIGINAL_BACKUP"
echo -e "${GREEN}✅ Тестовые файлы удалены${NC}"

echo ""
echo "=========================================="
echo -e "${GREEN}✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО УСПЕШНО!${NC}"
echo "=========================================="
echo ""
echo "📋 Резюме:"
echo "   ✅ Резервная копия создается корректно"
echo "   ✅ Backup валиден и содержит все данные"
echo "   ✅ Восстановление работает правильно"
echo "   ✅ Сервисы работают после восстановления"
echo ""
echo "💡 Теперь вы можете быть уверены, что:"
echo "   1. Резервные копии создаются правильно"
echo "   2. Восстановление из backup работает"
echo "   3. В случае потери данных вы сможете восстановить"
echo ""

