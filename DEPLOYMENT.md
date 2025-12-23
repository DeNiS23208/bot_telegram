# Инструкция по развертыванию бота на сервере

## Требования

- Сервер с Ubuntu/Debian (или другой Linux)
- Python 3.8 или выше
- Доступ по SSH с правами root или sudo
- Домен с SSL сертификатом (для webhook ЮKassa) или использование ngrok для тестирования

## Шаг 1: Подготовка сервера

### 1.1. Подключение к серверу

```bash
ssh root@your-server-ip
```

### 1.2. Обновление системы

```bash
apt update && apt upgrade -y
```

### 1.3. Установка Python и необходимых пакетов

```bash
apt install -y python3 python3-pip python3-venv git
```

## Шаг 2: Создание директории и клонирование проекта

### 2.1. Создание директории

```bash
mkdir -p /opt/bot_telegram
cd /opt/bot_telegram
```

### 2.2. Загрузка файлов проекта

Если у вас есть Git репозиторий:
```bash
git clone <your-repo-url> /opt/bot_telegram
```

Или загрузите файлы вручную через `scp`:
```bash
# С вашего локального компьютера
scp -r /Users/gdm/Documents/bot_telegram/* root@your-server-ip:/opt/bot_telegram/
```

## Шаг 3: Создание виртуального окружения

```bash
cd /opt/bot_telegram
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Шаг 4: Настройка переменных окружения

### 4.1. Создание файла .env

```bash
cd /opt/bot_telegram
nano .env
```

### 4.2. Добавьте следующие переменные:

```env
# Telegram Bot
BOT_TOKEN=your_telegram_bot_token_here
BOT_USERNAME=your_bot_username
CHANNEL_ID=your_channel_id

# ЮKassa
YOOKASSA_SHOP_ID=your_shop_id
YOOKASSA_SECRET_KEY=your_secret_key
YOOKASSA_RETURN_URL=https://your-domain.com/payment/return

# База данных
DB_PATH=/opt/bot_telegram/bot.db

# Email для платежей (для чеков 54-ФЗ)
# ВАЖНО: Это email, который будет использоваться в чеках, отправляемых пользователям при оплате.
# Рекомендуется использовать реальный email (ваш личный или корпоративный).
# Можно использовать один email для всех платежей или собирать email пользователя при оплате.
PAYMENT_CUSTOMER_EMAIL=your-email@example.com

# Опционально: пути к видео для приветствия
WELCOME_VIDEO_PATH=/opt/bot_telegram/welcome_video.mp4
WELCOME_VIDEO_GIF_PATH=/opt/bot_telegram/welcome_video.gif
WELCOME_VIDEO_URL=https://example.com/video.mp4
```

### 4.3. Как получить значения:

- **BOT_TOKEN**: Получите у [@BotFather](https://t.me/BotFather) в Telegram
- **BOT_USERNAME**: Имя бота без @ (например, `work232_bot`)
- **CHANNEL_ID**: 
  - Добавьте бота в канал как администратора
  - Отправьте любое сообщение в канал
  - Перейдите по ссылке: `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
  - Найдите `chat.id` в ответе (это и есть CHANNEL_ID)
- **YOOKASSA_SHOP_ID** и **YOOKASSA_SECRET_KEY**: Получите в личном кабинете ЮKassa
- **YOOKASSA_RETURN_URL**: URL вашего сервера с путем `/payment/return` (должен быть доступен по HTTPS)
  - **ВАЖНО:** URL должен заканчиваться на `/payment/return`
  - Пример: `https://your-domain.com/payment/return`
  - Этот URL используется для возврата пользователя после оплаты

### 4.4. Защита файла .env

```bash
chmod 600 .env
```

## Шаг 5: Настройка webhook для ЮKassa

### 5.1. Настройка домена и SSL

Если у вас есть домен:
1. Настройте DNS записи для вашего домена
2. Установите SSL сертификат (например, через Let's Encrypt):

```bash
apt install certbot python3-certbot-nginx -y
certbot certonly --standalone -d your-domain.com
```

### 5.2. Настройка Nginx (опционально, но рекомендуется)

```bash
apt install nginx -y
```

Создайте конфигурацию:

```bash
nano /etc/nginx/sites-available/bot_telegram
```

Добавьте:

```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Активируйте конфигурацию:

```bash
ln -s /etc/nginx/sites-available/bot_telegram /etc/nginx/sites-enabled/
nginx -t
systemctl restart nginx
```

### 5.3. Настройка webhook в личном кабинете ЮKassa

1. Войдите в личный кабинет ЮKassa
2. Перейдите в раздел "Настройки" → "Webhook"
3. Добавьте URL: `https://your-domain.com/yookassa/webhook`
4. Выберите события: `payment.succeeded`, `payment.canceled`, `refund.succeeded`

## Шаг 6: Настройка systemd сервисов

### 6.1. Копирование файлов сервисов

Файлы `telegram-bot.service` и `webhook.service` уже должны быть в проекте. Проверьте пути в них:

```bash
cat telegram-bot.service
cat webhook.service
```

### 6.2. Установка сервисов

```bash
cp telegram-bot.service /etc/systemd/system/
cp webhook.service /etc/systemd/system/
systemctl daemon-reload
```

### 6.3. Включение автозапуска

```bash
systemctl enable telegram-bot
systemctl enable webhook
```

### 6.4. Запуск сервисов

```bash
systemctl start telegram-bot
systemctl start webhook
```

### 6.5. Проверка статуса

```bash
systemctl status telegram-bot
systemctl status webhook
```

### 6.6. Просмотр логов

```bash
# Логи бота
journalctl -u telegram-bot -f

# Логи webhook
journalctl -u webhook -f

# Все логи вместе
journalctl -u telegram-bot -u webhook -f
```

## Шаг 7: Настройка файрвола (если используется)

```bash
# Разрешить HTTP и HTTPS
ufw allow 80/tcp
ufw allow 443/tcp

# Или для iptables
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT
```

## Шаг 8: Тестирование

### 8.1. Проверка работы бота

1. Найдите вашего бота в Telegram
2. Отправьте команду `/start`
3. Проверьте, что бот отвечает

### 8.2. Проверка webhook

```bash
# Проверка доступности webhook
curl https://your-domain.com/yookassa/webhook

# Или локально
curl http://localhost:8000/yookassa/webhook
```

### 8.3. Тестовый платеж

1. Создайте тестовый платеж через бота
2. Проверьте логи webhook на наличие событий от ЮKassa
3. Проверьте, что платеж обрабатывается корректно

## Шаг 9: Дополнительные настройки

### 9.1. Настройка приветственного видео (опционально)

Если хотите добавить приветственное видео:

```bash
# Загрузите видео на сервер
scp welcome_video.mp4 root@your-server-ip:/opt/bot_telegram/
scp welcome_video.gif root@your-server-ip:/opt/bot_telegram/

# Установите права
chmod 644 /opt/bot_telegram/welcome_video.*
```

### 9.2. Настройка автопродления подписок

Убедитесь, что в личном кабинете ЮKassa включены автоплатежи (recurring payments), если вы хотите использовать автопродление подписок.

## Управление сервисами

### Перезапуск сервисов

```bash
systemctl restart telegram-bot
systemctl restart webhook
```

### Остановка сервисов

```bash
systemctl stop telegram-bot
systemctl stop webhook
```

### Просмотр логов

```bash
# Последние 100 строк логов бота
journalctl -u telegram-bot -n 100

# Последние 100 строк логов webhook
journalctl -u webhook -n 100

# Логи в реальном времени
journalctl -u telegram-bot -u webhook -f
```

### Использование скрипта перезапуска

```bash
chmod +x restart_services.sh
./restart_services.sh
```

## Обновление бота

### 1. Остановите сервисы

```bash
systemctl stop telegram-bot
systemctl stop webhook
```

### 2. Обновите код

```bash
cd /opt/bot_telegram
git pull  # если используете Git
# или загрузите новые файлы через scp
```

### 3. Обновите зависимости (если изменились)

```bash
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Запустите сервисы

```bash
systemctl start telegram-bot
systemctl start webhook
```

## Решение проблем

### Бот не отвечает

1. Проверьте статус сервиса: `systemctl status telegram-bot`
2. Проверьте логи: `journalctl -u telegram-bot -f`
3. Проверьте правильность BOT_TOKEN в `.env`
4. Убедитесь, что бот запущен: `ps aux | grep bot.py`

### Webhook не работает

1. Проверьте статус: `systemctl status webhook`
2. Проверьте логи: `journalctl -u webhook -f`
3. Проверьте доступность URL: `curl https://your-domain.com/yookassa/webhook`
4. Убедитесь, что порт 8000 открыт: `netstat -tlnp | grep 8000`
5. Проверьте настройки webhook в личном кабинете ЮKassa

### Платежи не обрабатываются

1. Проверьте логи webhook на наличие событий от ЮKassa
2. Убедитесь, что webhook URL правильно настроен в ЮKassa
3. Проверьте правильность YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY
4. Проверьте, что SSL сертификат действителен

### Проблемы с базой данных

1. Проверьте права доступа к файлу БД: `ls -la /opt/bot_telegram/bot.db`
2. Убедитесь, что путь к БД правильный в `.env`
3. При необходимости пересоздайте БД (осторожно, это удалит все данные):
   ```bash
   rm /opt/bot_telegram/bot.db
   systemctl restart telegram-bot
   ```

## Безопасность

1. **Никогда не публикуйте файл `.env`** - он содержит секретные ключи
2. Используйте сильные пароли для SSH
3. Настройте файрвол для ограничения доступа
4. Регулярно обновляйте систему и зависимости
5. Используйте HTTPS для всех внешних соединений
6. Ограничьте доступ к директории проекта: `chmod 700 /opt/bot_telegram`

## Резервное копирование

Рекомендуется настроить регулярное резервное копирование:

```bash
# Создайте скрипт резервного копирования
nano /opt/bot_telegram/backup.sh
```

Добавьте:

```bash
#!/bin/bash
BACKUP_DIR="/opt/backups/bot_telegram"
mkdir -p $BACKUP_DIR
DATE=$(date +%Y%m%d_%H%M%S)
cp /opt/bot_telegram/bot.db $BACKUP_DIR/bot_${DATE}.db
# Храним только последние 7 дней
find $BACKUP_DIR -name "bot_*.db" -mtime +7 -delete
```

Сделайте исполняемым и добавьте в cron:

```bash
chmod +x /opt/bot_telegram/backup.sh
crontab -e
# Добавьте строку для ежедневного бэкапа в 3:00
0 3 * * * /opt/bot_telegram/backup.sh
```

## Поддержка

При возникновении проблем:
1. Проверьте логи сервисов
2. Убедитесь, что все переменные окружения настроены правильно
3. Проверьте документацию ЮKassa и Telegram Bot API

