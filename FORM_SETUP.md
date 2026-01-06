# Настройка собственной HTML формы

## Что было сделано

1. ✅ Создана HTML форма (`templates/form.html`) с полями:
   - Имя (обязательное)
   - Телефон (обязательное)
   - Email (обязательное)
   - Город проживания (обязательное)
   - Пол (обязательное, выпадающий список)
   - Направление вашей деятельности (обязательное, текстовое поле)
   - Чекбокс согласия на обработку персональных данных
   - Чекбокс согласия с условиями Оферты
   - Кнопка "Записаться"

2. ✅ Добавлен фон с логотипом (`static/LOGOTIP.png`)

3. ✅ Созданы endpoints в `webhook_app.py`:
   - `GET /form` - отображение формы
   - `POST /form/submit` - обработка отправки формы

4. ✅ Добавлены зависимости в `requirements.txt`:
   - `jinja2==3.1.4`
   - `python-multipart==0.0.9`

## Что нужно сделать для активации

### 1. Установить зависимости на сервере

```bash
ssh root@178.72.153.64
cd /opt/bot_telegram
source venv/bin/activate
pip install jinja2==3.1.4 python-multipart==0.0.9
```

### 2. Скопировать файлы на сервер

```bash
# С локального компьютера
scp -r templates/ static/ root@178.72.153.64:/opt/bot_telegram/
scp webhook_app.py root@178.72.153.64:/opt/bot_telegram/
```

### 3. Обновить bot.py на сервере

В файле `bot.py` на строке 210 заменить:

```python
# БЫЛО:
form_url = f"https://forms.yandex.ru/u/69592c7e068ff04fd8f00241/?token={form_token}"

# ДОЛЖНО БЫТЬ:
form_url = f"https://xasanim.ru/form?token={form_token}&telegram_id={telegram_id}"
```

### 4. Перезапустить сервисы

```bash
ssh root@178.72.153.64
systemctl restart telegram-bot webhook
systemctl status telegram-bot webhook
```

## Проверка работы

1. Откройте в браузере: `https://xasanim.ru/form?token=TEST&telegram_id=123`
   - Должна отобразиться форма с логотипом на фоне

2. Проверьте статические файлы:
   - `https://xasanim.ru/static/LOGOTIP.png` - должен отображаться логотип

3. Протестируйте отправку формы через бота

## Структура файлов

```
/opt/bot_telegram/
├── templates/
│   └── form.html          # HTML форма
├── static/
│   └── LOGOTIP.png        # Логотип для фона
├── webhook_app.py         # Endpoints для формы
└── bot.py                 # Нужно обновить URL формы
```

## Особенности

- Все поля обязательные для заполнения
- Валидация на клиенте (JavaScript)
- Валидация на сервере (Python)
- Автоматическая отправка уведомления в бот после заполнения
- Фон с логотипом (полупрозрачный)
- Адаптивный дизайн (работает на мобильных устройствах)

