#!/bin/bash
# Скрипт для обновления кода на сервере из репозитория

set -e

SERVER_IP="178.72.153.64"
SERVER_USER="root"
SERVER_PASSWORD="uWawa8wwzCoa"
REPO_DIR="/opt/bot_telegram"

echo "🔄 Обновление кода на сервере..."

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
        send "echo '📥 Получение изменений из репозитория...'\r"
        expect "# "
        send "git pull origin main\r"
        expect "# "
        send "echo '---'\r"
        expect "# "
        send "echo '🔄 Перезапуск сервисов...'\r"
        expect "# "
        send "systemctl restart telegram-bot webhook\r"
        expect "# "
        send "sleep 2\r"
        expect "# "
        send "systemctl status telegram-bot webhook --no-pager | head -12\r"
        expect "# "
        send "echo '---'\r"
        expect "# "
        send "echo '✅ Сервер обновлен и перезапущен'\r"
        expect "# "
        send "exit\r"
    }
    timeout {
        exit 1
    }
    eof
}
EOF

echo "✅ Обновление завершено"

