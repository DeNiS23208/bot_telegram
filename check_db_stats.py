#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect('/opt/bot_telegram/bot.db')
cursor = conn.cursor()

print("=== СТАТИСТИКА БАЗЫ ДАННЫХ ===")

# Пользователи
cursor.execute('SELECT COUNT(*) FROM users')
users_count = cursor.fetchone()[0]
print(f'👥 Всего пользователей: {users_count}')

# Подписки
cursor.execute('SELECT COUNT(*) FROM subscriptions WHERE expires_at > datetime("now", "utc")')
active_subs = cursor.fetchone()[0]
print(f'✅ Активных подписок: {active_subs}')

# Платежи
cursor.execute('SELECT COUNT(*) FROM payments WHERE status = "succeeded"')
success_payments = cursor.fetchone()[0]
print(f'💰 Успешных платежей: {success_payments}')

# Формы
cursor.execute('SELECT COUNT(*) FROM daily_form_submissions')
forms_count = cursor.fetchone()[0]
print(f'📝 Заполненных форм: {forms_count}')

# Автопродление
cursor.execute('SELECT COUNT(*) FROM subscriptions WHERE auto_renewal_enabled = 1 AND expires_at > datetime("now", "utc")')
auto_renewal_count = cursor.fetchone()[0]
print(f'🔄 Автопродление включено: {auto_renewal_count}')

# Ссылки-приглашения
cursor.execute('SELECT COUNT(*) FROM invite_links WHERE revoked = 0')
active_links = cursor.fetchone()[0]
print(f'🔗 Активных ссылок-приглашений: {active_links}')

# Напоминания (новое поле)
try:
    cursor.execute('SELECT COUNT(*) FROM invite_links WHERE reminder_sent = 0 AND revoked = 0 AND created_at <= datetime("now", "-1 hour", "utc")')
    pending_reminders = cursor.fetchone()[0]
    print(f'⏰ Ожидающих напоминаний: {pending_reminders}')
except Exception as e:
    print(f'⏰ Поле reminder_sent: {e}')

print("\n=== ПОСЛЕДНИЕ АКТИВНОСТИ ===")

# Последние пользователи
cursor.execute('SELECT telegram_id, username, created_at FROM users ORDER BY created_at DESC LIMIT 5')
print('📅 Последние 5 зарегистрированных пользователей:')
for row in cursor.fetchall():
    print(f'  - ID: {row[0]}, Username: {row[1] or "N/A"}, Дата: {row[2]}')

# Последние платежи
cursor.execute('SELECT telegram_id, status, created_at FROM payments ORDER BY created_at DESC LIMIT 5')
print('\n💳 Последние 5 платежей:')
for row in cursor.fetchall():
    print(f'  - ID: {row[0]}, Статус: {row[1]}, Дата: {row[2]}')

conn.close()
