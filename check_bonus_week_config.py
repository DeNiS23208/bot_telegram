#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect('/opt/bot_telegram/bot.db')
cursor = conn.cursor()

# Проверяем дату окончания бонусной недели
cursor.execute('SELECT start_time FROM bonus_week_config WHERE id = 1')
row = cursor.fetchone()
if row:
    print(f'📅 Время начала бонусной недели в БД: {row[0]}')
else:
    print('⚠️ Нет записи о начале бонусной недели в БД')

# Проверяем подписки
cursor.execute('SELECT COUNT(*) FROM subscriptions WHERE expires_at IS NOT NULL')
total_subs = cursor.fetchone()[0]
print(f'📊 Всего подписок в БД: {total_subs}')

# Проверяем подписки, которые истекают завтра
cursor.execute("""
    SELECT COUNT(*) 
    FROM subscriptions 
    WHERE expires_at LIKE '2026-01-14%'
""")
tomorrow_subs = cursor.fetchone()[0]
print(f'📅 Подписок, истекающих 14.01.2026: {tomorrow_subs}')

# Проверяем уникальные даты окончания
cursor.execute("""
    SELECT DISTINCT expires_at 
    FROM subscriptions 
    WHERE expires_at IS NOT NULL
    ORDER BY expires_at
    LIMIT 10
""")
print('\n📋 Уникальные даты окончания подписок (первые 10):')
for row in cursor.fetchall():
    print(f'  - {row[0]}')

conn.close()
