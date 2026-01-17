#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/opt/bot_telegram/bot.db')
cursor = conn.cursor()

cursor.execute("""
    SELECT telegram_id, status, created_at, payment_id
    FROM payments
    WHERE created_at >= '2026-01-14 10:58:00'
    ORDER BY created_at DESC
    LIMIT 20
""")

print('Последние 20 платежей после окончания бонусной недели:')
for row in cursor.fetchall():
    telegram_id, status, created_at, payment_id = row
    created_short = created_at[:19] if created_at else 'N/A'
    print(f'  - ID: {telegram_id}, Статус: {status}, Время: {created_short}')

# Успешные
cursor.execute("""
    SELECT COUNT(*) 
    FROM payments 
    WHERE status = 'succeeded' 
    AND created_at >= '2026-01-14 10:58:00'
""")
success_count = cursor.fetchone()[0]
print(f'\n✅ Успешных платежей: {success_count}')

# Отмененные
cursor.execute("""
    SELECT COUNT(*) 
    FROM payments 
    WHERE status = 'canceled' 
    AND created_at >= '2026-01-14 10:58:00'
""")
canceled_count = cursor.fetchone()[0]
print(f'❌ Отмененных платежей: {canceled_count}')

conn.close()
