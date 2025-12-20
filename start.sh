#!/bin/bash

# Скрипт для запуска бота и webhook

# Запуск бота в фоне
python3 bot.py &
BOT_PID=$!

# Запуск webhook сервера
uvicorn webhook_app:app --host 0.0.0.0 --port 8000

# Если webhook остановится, остановим и бота
kill $BOT_PID

