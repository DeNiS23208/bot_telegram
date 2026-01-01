#!/usr/bin/expect -f

set timeout 300
set server "178.72.153.64"
set user "root"
set password "uWawa8wwzCoa"

puts "🔌 Подключение к серверу $user@$server..."

spawn ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null $user@$server

expect {
    "password:" {
        send "$password\r"
        exp_continue
    }
    "yes/no" {
        send "yes\r"
        exp_continue
    }
    "# " {
        puts "✅ Подключено! Начинаю настройку..."
        
        # Обновление системы
        send "apt update && apt upgrade -y\r"
        expect "# "
        
        # Установка необходимых пакетов
        send "apt install -y python3 python3-pip python3-venv git curl nginx certbot python3-certbot-nginx\r"
        expect "# "
        
        # Создание директории
        send "mkdir -p /opt/bot_telegram\r"
        expect "# "
        
        puts "✅ Базовая настройка завершена"
        puts "📋 Следующий шаг: загрузите файлы проекта на сервер"
        
        send "exit\r"
        expect eof
    }
    timeout {
        puts "❌ Таймаут подключения"
        exit 1
    }
    "Permission denied" {
        puts "❌ Ошибка доступа. Проверьте пароль."
        exit 1
    }
}

puts "✅ Готово!"

