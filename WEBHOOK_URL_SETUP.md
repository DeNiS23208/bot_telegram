# Настройка WEBHOOK_BASE_URL

## Что такое WEBHOOK_BASE_URL?

`WEBHOOK_BASE_URL` - это URL вашего сервера, где запущен `webhook_app.py` (FastAPI приложение).

## Как определить WEBHOOK_BASE_URL?

### Вариант 1: Если у вас есть домен
```
WEBHOOK_BASE_URL=https://your-domain.com
```
Например: `https://example.com` или `https://api.yourdomain.ru`

### Вариант 2: Если используете IP адрес
```
WEBHOOK_BASE_URL=http://YOUR_SERVER_IP:8000
```
Например: `http://123.45.67.89:8000`

**Важно:** Если используете IP, убедитесь, что:
- Порт 8000 открыт в firewall
- Сервер доступен из интернета

### Вариант 3: Если используете ngrok или другой туннель
```
WEBHOOK_BASE_URL=https://your-ngrok-url.ngrok.io
```
Например: `https://abc123.ngrok.io`

## Где указать WEBHOOK_BASE_URL?

Добавьте в файл `.env` на сервере:

```env
WEBHOOK_BASE_URL=https://your-domain.com
```

Или если используете IP:
```env
WEBHOOK_BASE_URL=http://YOUR_SERVER_IP:8000
```

## Как проверить, что URL правильный?

1. Убедитесь, что `webhook_app.py` запущен на сервере:
   ```bash
   uvicorn webhook_app:app --host 0.0.0.0 --port 8000
   ```

2. Проверьте доступность endpoint:
   ```bash
   curl http://YOUR_SERVER_IP:8000/payment/return
   ```
   Должен вернуть редирект на бота.

3. В логах webhook_app.py должны быть сообщения о запросах на `/payment/return`

## Примеры

### Если сервер на домене example.com:
```env
WEBHOOK_BASE_URL=https://example.com
```

### Если сервер на IP 192.168.1.100:
```env
WEBHOOK_BASE_URL=http://192.168.1.100:8000
```

### Если используете ngrok:
```env
WEBHOOK_BASE_URL=https://abc123.ngrok.io
```

## Важно!

- URL должен быть доступен из интернета (для ЮKassa)
- Если используете HTTPS, нужен SSL сертификат
- После изменения `.env` перезапустите бота и webhook

