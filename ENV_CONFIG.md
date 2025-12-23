# Конфигурация окружения (.env)

## Важные замечания

⚠️ **ВНИМАНИЕ:** Этот файл содержит конфиденциальную информацию. НЕ коммитьте его в Git!

## Текущая конфигурация

```env
BOT_TOKEN=8401625509:AAGPQaVJCBgq3mg3QVRh8ZLzRe87MDdn0E8
CHANNEL_ID=-1003235245037
BOT_USERNAME=xasanimbot
YOOKASSA_SHOP_ID=1214089
YOOKASSA_SECRET_KEY=live_xqrW7eHtiNmlzKHxfEjjVOEwExI0M4xGsGQz5H16xrk
PAYMENT_CUSTOMER_EMAIL=test@example.com
DB_PATH=/opt/bot_telegram/bot.db
YOOKASSA_RETURN_URL=https://xasanim.ru

# Welcome video URL
WELCOME_VIDEO_URL=https://xasanim.ru/videos/welcome_video.mp4
```

## Важные исправления

### 1. YOOKASSA_RETURN_URL

**Текущее значение:** `https://xasanim.ru`

**Проблема:** В коде ожидается путь `/payment/return` для обработки возврата после оплаты.

**Исправление:** Должно быть:
```env
YOOKASSA_RETURN_URL=https://xasanim.ru/payment/return
```

### 2. PAYMENT_CUSTOMER_EMAIL

**Текущее значение:** `test@example.com`

**Рекомендация:** Замените на реальный email, на который будут приходить чеки от ЮKassa.

**Варианты:**
- Ваш личный email
- Email для платежей: `payments@xasanim.ru` или `info@xasanim.ru`
- Любой рабочий email, который вы проверяете

**Важно:** Этот email используется в чеках 54-ФЗ, которые отправляются пользователям при оплате. ЮKassa может использовать его для отправки уведомлений о платежах.

## Проверка конфигурации

После настройки на сервере проверьте:

1. **Webhook URL в ЮKassa:** `https://xasanim.ru/yookassa/webhook`
2. **Return URL:** `https://xasanim.ru/payment/return` (должен быть доступен)
3. **SSL сертификат:** Должен быть валидным для домена `xasanim.ru`

## Безопасность

- ✅ Используется продакшн ключ ЮKassa (`live_`)
- ✅ Домен настроен: `xasanim.ru`
- ⚠️ Рекомендуется заменить `test@example.com` на реальный email

