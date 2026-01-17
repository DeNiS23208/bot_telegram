#!/usr/bin/env python3
import sqlite3

conn = sqlite3.connect('/opt/bot_telegram/bot.db')
cursor = conn.cursor()

support_id = 8429417659

print('Удаление аккаунта otd_zabota (ID: 8429417659) из базы данных...')
print()

# Удаляем из всех таблиц
print('1. Удаление из таблицы users...')
cursor.execute('DELETE FROM users WHERE telegram_id = ?', (support_id,))
print(f'   Удалено: {cursor.rowcount} записей')

print('2. Удаление из таблицы subscriptions...')
cursor.execute('DELETE FROM subscriptions WHERE telegram_id = ?', (support_id,))
print(f'   Удалено: {cursor.rowcount} записей')

print('3. Удаление из таблицы payments...')
cursor.execute('DELETE FROM payments WHERE telegram_id = ?', (support_id,))
print(f'   Удалено: {cursor.rowcount} записей')

print('4. Удаление из таблицы invite_links...')
cursor.execute('DELETE FROM invite_links WHERE telegram_user_id = ?', (support_id,))
print(f'   Удалено: {cursor.rowcount} записей')

print('5. Удаление из таблицы approved_users...')
cursor.execute('DELETE FROM approved_users WHERE telegram_user_id = ?', (support_id,))
print(f'   Удалено: {cursor.rowcount} записей')

print('6. Удаление из таблицы daily_form_submissions...')
cursor.execute('DELETE FROM daily_form_submissions WHERE telegram_id = ?', (support_id,))
print(f'   Удалено: {cursor.rowcount} записей')

# Проверяем наличие записей
print()
print('Проверка наличия записей после удаления:')
cursor.execute('SELECT COUNT(*) FROM users WHERE telegram_id = ?', (support_id,))
print(f'   users: {cursor.fetchone()[0]}')
cursor.execute('SELECT COUNT(*) FROM subscriptions WHERE telegram_id = ?', (support_id,))
print(f'   subscriptions: {cursor.fetchone()[0]}')
cursor.execute('SELECT COUNT(*) FROM payments WHERE telegram_id = ?', (support_id,))
print(f'   payments: {cursor.fetchone()[0]}')
cursor.execute('SELECT COUNT(*) FROM invite_links WHERE telegram_user_id = ?', (support_id,))
print(f'   invite_links: {cursor.fetchone()[0]}')
cursor.execute('SELECT COUNT(*) FROM approved_users WHERE telegram_user_id = ?', (support_id,))
print(f'   approved_users: {cursor.fetchone()[0]}')
cursor.execute('SELECT COUNT(*) FROM daily_form_submissions WHERE telegram_id = ?', (support_id,))
print(f'   daily_form_submissions: {cursor.fetchone()[0]}')

conn.commit()
conn.close()

print()
print('✅ Аккаунт otd_zabota полностью удален из базы данных!')
