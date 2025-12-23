#!/bin/bash
# Скрипт для быстрой очистки БД для тестов

echo "🔄 Очистка базы данных для тестов..."

expect << 'EOF'
set timeout 30
spawn ssh -o StrictHostKeyChecking=no root@178.72.153.64
expect {
    "password:" {
        send "uWawa8wwzCoa\r"
        exp_continue
    }
    "# " {
        send "cd /opt/bot_telegram\r"
        expect "# "
        send "python3 clear_db.py --full << 'PYTHON_INPUT'\r"
        expect "# "
        send "yes\r"
        expect "# "
        send "PYTHON_INPUT\r"
        expect "# "
        send "echo '---'\r"
        expect "# "
        send "echo '✅ База данных очищена для тестов'\r"
        expect "# "
        send "exit\r"
    }
    timeout {
        exit 1
    }
    eof
}
EOF

echo "✅ Готово! База данных очищена."

