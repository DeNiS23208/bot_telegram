#!/bin/bash
# Скрипт для подключения к серверу и автоматической настройки

SERVER_IP="178.72.153.64"
SERVER_USER="root"
SERVER_PASSWORD="uWawa8wwzCoa"

echo "🔌 Подключение к серверу $SERVER_USER@$SERVER_IP..."

# Функция для выполнения команд на удаленном сервере
execute_remote() {
    sshpass -p "$SERVER_PASSWORD" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$SERVER_USER@$SERVER_IP" "$1"
}

# Проверка sshpass
if ! command -v sshpass &> /dev/null; then
    echo "❌ sshpass не установлен. Устанавливаю..."
    # Попробуем установить через разные методы
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "⚠️ На macOS sshpass требует ручной установки или использования expect"
        echo "Попробую использовать expect..."
        
        # Используем expect для автоматического ввода пароля
        expect << EOF
set timeout 30
spawn ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" "echo 'Connected successfully'"
expect {
    "password:" {
        send "$SERVER_PASSWORD\r"
        exp_continue
    }
    "Connected successfully" {
        puts "✅ Подключение успешно!"
        exit 0
    }
    timeout {
        puts "❌ Таймаут подключения"
        exit 1
    }
}
EOF
    fi
else
    echo "✅ sshpass найден, подключаюсь..."
    execute_remote "echo 'Connected successfully'"
fi

