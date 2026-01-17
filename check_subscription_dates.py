#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone, timedelta, timedelta

DB_PATH = "/opt/bot_telegram/bot.db"
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Всего пользователей
cursor.execute("SELECT COUNT(*) FROM users")
total_users = cursor.fetchone()[0]
print(f"Всего пользователей в базе: {total_users}")
print()

# Пользователи с подписками
cursor.execute("SELECT COUNT(DISTINCT telegram_id) FROM subscriptions WHERE expires_at IS NOT NULL")
users_with_subs = cursor.fetchone()[0]
print(f"Пользователей с подписками: {users_with_subs}")
print()

# Проверяем даты окончания всех подписок
cursor.execute("SELECT DISTINCT expires_at FROM subscriptions WHERE expires_at IS NOT NULL ORDER BY expires_at")
expires_dates = cursor.fetchall()
print(f"Уникальных дат окончания: {len(expires_dates)}")
print()

bonus_end = datetime(2026, 1, 14, 10, 58, 42, tzinfo=timezone.utc)
bonus_end_seconds = bonus_end.replace(microsecond=0)

for date_tuple in expires_dates:
    expires_at_str = date_tuple[0]
    expires_at = datetime.fromisoformat(expires_at_str)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    
    # Считаем сколько пользователей с этой датой
    cursor.execute("SELECT COUNT(*) FROM subscriptions WHERE expires_at = ?", (expires_at_str,))
    count = cursor.fetchone()[0]
    
    expires_seconds = expires_at.replace(microsecond=0)
    
    # Преобразуем в МСК (UTC+3)
    moscow_time = expires_at.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3)))
    
    if expires_seconds == bonus_end_seconds:
        print(f"  ✅ {moscow_time.strftime('%d.%m.%Y %H:%M:%S')} МСК: {count} пользователей (БОНУСНАЯ НЕДЕЛЯ)")
    else:
        print(f"  ⚠️ {moscow_time.strftime('%d.%m.%Y %H:%M:%S')} МСК: {count} пользователей (ДРУГАЯ ДАТА)")

conn.close()
