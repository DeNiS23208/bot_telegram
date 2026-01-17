#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/opt/bot_telegram/bot.db')
cursor = conn.cursor()

# Пользователи с автопродлением
cursor.execute("""
    SELECT 
        s.telegram_id,
        u.username,
        s.auto_renewal_enabled,
        s.saved_payment_method_id,
        s.expires_at,
        s.auto_renewal_attempts
    FROM subscriptions s
    LEFT JOIN users u ON s.telegram_id = u.telegram_id
    WHERE s.expires_at > datetime("now", "utc")
    ORDER BY s.auto_renewal_enabled DESC, s.telegram_id
""")

print("=== ПОЛЬЗОВАТЕЛИ С АКТИВНЫМИ ПОДПИСКАМИ ===\n")

all_subs = cursor.fetchall()

# С автопродлением
with_auto = [s for s in all_subs if s[2] == 1]
# Без автопродления
without_auto = [s for s in all_subs if s[2] == 0]

print(f"✅ С АВТОПРОДЛЕНИЕМ: {len(with_auto)} пользователей")
for sub in with_auto:
    telegram_id, username, auto_renewal, saved_method, expires_at, attempts = sub
    has_saved_method = "✅" if saved_method else "❌"
    print(f"  - ID: {telegram_id}, Username: {username or 'N/A'}, Сохраненная карта: {has_saved_method}, Попытки: {attempts}")

print()
print(f"❌ БЕЗ АВТОПРОДЛЕНИЯ: {len(without_auto)} пользователей")
for sub in without_auto:
    telegram_id, username, auto_renewal, saved_method, expires_at, attempts = sub
    print(f"  - ID: {telegram_id}, Username: {username or 'N/A'}")

conn.close()
