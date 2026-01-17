#!/usr/bin/env python3
import sqlite3
import os
from datetime import datetime, timedelta, timezone

DB_PATH = os.getenv("DB_PATH", "/opt/bot_telegram/bot.db")

try:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT start_time, updated_at FROM bonus_week_config WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    
    if row:
        start_time_str = row[0]
        updated_at = row[1] if len(row) > 1 else None
        start_time = datetime.fromisoformat(start_time_str)
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        
        # Вычисляем конец (7 дней)
        end_time = start_time + timedelta(days=7)
        
        print(f"Время начала в БД: {start_time}")
        print(f"Время окончания (вычислено): {end_time}")
        print(f"Обновлено в БД: {updated_at}")
    else:
        print("В БД нет времени начала бонусной недели")
except Exception as e:
    print(f"Ошибка: {e}")
