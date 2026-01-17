#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/opt/bot_telegram/bot.db')
cursor = conn.cursor()

# Подписки с автопродлением и их попытки
cursor.execute("""
    SELECT 
        s.telegram_id,
        u.username,
        s.auto_renewal_enabled,
        s.auto_renewal_attempts,
        s.expires_at,
        s.saved_payment_method_id
    FROM subscriptions s
    LEFT JOIN users u ON s.telegram_id = u.telegram_id
    WHERE s.auto_renewal_enabled = 1
    ORDER BY s.auto_renewal_attempts DESC, s.telegram_id
""")

print('📊 ПОЛЬЗОВАТЕЛИ С АВТОПРОДЛЕНИЕМ (по количеству попыток):')
print()

subs = cursor.fetchall()

# Группируем по попыткам
by_attempts = {}
for sub in subs:
    attempts = sub[3] or 0
    if attempts not in by_attempts:
        by_attempts[attempts] = []
    by_attempts[attempts].append(sub)

for attempts in sorted(by_attempts.keys(), reverse=True):
    count = len(by_attempts[attempts])
    print(f'🔄 Попыток: {attempts} - {count} пользователей')
    for sub in by_attempts[attempts][:5]:  # Показываем первые 5
        telegram_id, username, auto_renewal, attempts_val, expires_at, saved_method = sub
        expires_short = expires_at[:16] if expires_at else "N/A"
        print(f'  - ID: {telegram_id}, Username: {username or "N/A"}, Истекает: {expires_short}')
    if len(by_attempts[attempts]) > 5:
        print(f'  ... и еще {len(by_attempts[attempts]) - 5} пользователей')
    print()

# Активные подписки
cursor.execute('SELECT COUNT(*) FROM subscriptions WHERE expires_at > datetime("now", "utc")')
active = cursor.fetchone()[0]
print(f'✅ Активных подписок: {active}')

# Истекшие подписки
cursor.execute('SELECT COUNT(*) FROM subscriptions WHERE expires_at <= datetime("now", "utc")')
expired = cursor.fetchone()[0]
print(f'❌ Истекших подписок: {expired}')

# Успешные автоплатежи
cursor.execute("""
    SELECT COUNT(*) 
    FROM payments 
    WHERE status = 'succeeded' 
    AND created_at >= '2026-01-14 10:58:00'
    AND description LIKE '%автопродление%'
""")
auto_success = cursor.fetchone()[0]
print(f'✅ Успешных автоплатежей (после 10:58): {auto_success}')

conn.close()
