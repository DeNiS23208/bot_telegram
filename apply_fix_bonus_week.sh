#!/bin/bash
# Скрипт для применения исправлений бонусной недели на сервере

set -e

SERVER_IP="178.72.153.64"
SERVER_USER="root"
SERVER_PASSWORD="uWawa8wwzCoa"
REPO_DIR="/opt/bot_telegram"

echo "🔄 Применение исправлений на сервере..."

# Загружаем скрипт исправления БД
expect << EOF
set timeout 300
spawn scp -o StrictHostKeyChecking=no "/Users/gdm/Documents/bot_telegram/fix_bonus_week_expires.py" $SERVER_USER@$SERVER_IP:$REPO_DIR/
expect {
    "password:" {
        send "$SERVER_PASSWORD\r"
        exp_continue
    }
    "100%" {
        expect eof
    }
    timeout {
        exit 1
    }
    eof
}
EOF

echo "✅ Скрипт загружен на сервер"

# Подключаемся и запускаем исправление
expect << EOF
set timeout 300
spawn ssh -o StrictHostKeyChecking=no $SERVER_USER@$SERVER_IP
expect {
    "password:" {
        send "$SERVER_PASSWORD\r"
        exp_continue
    }
    "# " {
        send "cd $REPO_DIR\r"
        expect "# "
        send "echo '🔧 Запуск исправления дат в БД...'\r"
        expect "# "
        send "python3 fix_bonus_week_expires.py\r"
        expect "# "
        send "echo '✅ Исправление завершено'\r"
        expect "# "
        send "exit\r"
    }
    timeout {
        exit 1
    }
    eof
}
EOF

echo "✅ Исправления применены на сервере"
