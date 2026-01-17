#!/usr/bin/env python3
import sqlite3
from datetime import datetime

conn = sqlite3.connect('/opt/bot_telegram/bot.db')
cursor = conn.cursor()

print("=== ТЕКУЩЕЕ СОСТОЯНИЕ БАЗЫ ДАННЫХ ===\n")

# Активные подписки
cursor.execute('SELECT COUNT(*) FROM subscriptions WHERE expires_at > datetime("now", "utc")')
active = cursor.fetchone()[0]
print(f'✅ Активных подписок: {active}')

# Истекшие подписки
cursor.execute('SELECT COUNT(*) FROM subscriptions WHERE expires_at <= datetime("now", "utc")')
expired = cursor.fetchone()[0]
print(f'❌ Истекших подписок: {expired}')

# Пользователи с автопродлением
cursor.execute('SELECT COUNT(*) FROM subscriptions WHERE auto_renewal_enabled = 1')
auto_total = cursor.fetchone()[0]
print(f'🔄 Всего с автопродлением: {auto_total}')

# Попытки автопродления
cursor.execute("""
    SELECT 
        auto_renewal_attempts,
        COUNT(*) as count
    FROM subscriptions
    WHERE auto_renewal_enabled = 1
    GROUP BY auto_renewal_attempts
    ORDER BY auto_renewal_attempts DESC
""")
print('\n📊 Попытки автопродления:')
for row in cursor.fetchall():
    attempts, count = row
    print(f'  - Попыток: {attempts or 0} - {count} пользователей')

# Успешные платежи после окончания бонусной недели
cursor.execute("""
    SELECT COUNT(*) 
    FROM payments 
    WHERE status = 'succeeded' 
    AND created_at >= '2026-01-14 10:58:00'
""")
success_count = cursor.fetchone()[0]
print(f'\n✅ Успешных платежей (после 10:58): {success_count}')

# Отмененные платежи
cursor.execute("""
    SELECT COUNT(*) 
    FROM payments 
    WHERE status = 'canceled' 
    AND created_at >= '2026-01-14 10:58:00'
""")
canceled_count = cursor.fetchone()[0]
print(f'❌ Отмененных платежей (после 10:58): {canceled_count}')

# Последние платежи
cursor.execute("""
    SELECT telegram_id, status, created_at
    FROM payments
    WHERE created_at >= '2026-01-14 10:58:00'
    ORDER BY created_at DESC
    LIMIT 10
""")
print('\n📋 Последние 10 платежей:')
for row in cursor.fetchall():
    telegram_id, status, created_at = row
    created_short = created_at[:19] if created_at else 'N/A'
    status_icon = '✅' if status == 'succeeded' else '❌'
    print(f'  {status_icon} ID: {telegram_id}, Статус: {status}, Время: {created_short}')

# Подписки, которые были продлены (истекают в будущем)
cursor.execute("""
    SELECT COUNT(*)
    FROM subscriptions
    WHERE expires_at > datetime('now', '+30 days', 'utc')
""")
extended = cursor.fetchone()[0]
print(f'\n📅 Подписок продлено (истекают >30 дней): {extended}')

conn.close()
