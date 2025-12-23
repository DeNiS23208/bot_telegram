# Быстрый старт - Развертывание бота на сервере

## Минимальные шаги для запуска

### 1. Подключитесь к серверу

```bash
ssh root@your-server-ip
```

### 2. Загрузите файлы проекта на сервер

```bash
# С вашего локального компьютера
scp -r /Users/gdm/Documents/bot_telegram/* root@your-server-ip:/opt/bot_telegram/
```

### 3. Запустите скрипт настройки

```bash
ssh root@your-server-ip
cd /opt/bot_telegram
chmod +x setup_server.sh
./setup_server.sh
```

### 4. Настройте переменные окружения

```bash
nano /opt/bot_telegram/.env
```

Добавьте необходимые переменные (см. DEPLOYMENT.md для подробностей):

```env
BOT_TOKEN=your_token
BOT_USERNAME=your_bot_username
CHANNEL_ID=your_channel_id
YOOKASSA_SHOP_ID=your_shop_id
YOOKASSA_SECRET_KEY=your_secret_key
YOOKASSA_RETURN_URL=https://your-domain.com/payment/return
DB_PATH=/opt/bot_telegram/bot.db
PAYMENT_CUSTOMER_EMAIL=your-email@example.com
```

### 5. Запустите сервисы

```bash
systemctl enable telegram-bot webhook
systemctl start telegram-bot webhook
systemctl status telegram-bot webhook
```

### 6. Проверьте работу

- Найдите бота в Telegram и отправьте `/start`
- Проверьте логи: `journalctl -u telegram-bot -u webhook -f`

## Настройка webhook для ЮKassa

1. Убедитесь, что ваш домен доступен по HTTPS
2. В личном кабинете ЮKassa добавьте webhook: `https://your-domain.com/yookassa/webhook`
3. Выберите события: `payment.succeeded`, `payment.canceled`, `refund.succeeded`

## Полезные команды

```bash
# Перезапуск сервисов
systemctl restart telegram-bot webhook

# Просмотр логов
journalctl -u telegram-bot -u webhook -f

# Остановка сервисов
systemctl stop telegram-bot webhook
```

## Полная инструкция

Для подробной информации см. файл `DEPLOYMENT.md`

