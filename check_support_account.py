#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone, timedelta

conn = sqlite3.connect('/opt/bot_telegram/bot.db')
cursor = conn.cursor()

telegram_id = 8429417659

print(f"=== ПРОВЕРКА АККАУНТА ПОДДЕРЖКИ (otd_zabota, ID: {telegram_id}) ===\n")

# Проверяем пользователя
cursor.execute("""
    SELECT 
        u.telegram_id,
        u.username,
        u.created_at,
        s.auto_renewal_enabled,
        s.expires_at,
        s.starts_at,
        s.saved_payment_method_id,
        s.auto_renewal_attempts
    FROM users u
    LEFT JOIN subscriptions s ON u.telegram_id = s.telegram_id
    WHERE u.telegram_id = ?
""", (telegram_id,))

user = cursor.fetchone()

if user:
    telegram_id, username, created_at, auto_renewal, expires_at, starts_at, saved_method, attempts = user
    
    print(f"👤 Username: {username or 'N/A'}")
    print(f"📅 Создан: {created_at}")
    print(f"🔄 Автопродление: {'✅ ВКЛЮЧЕНО' if auto_renewal else '❌ ОТКЛЮЧЕНО'}")
    print(f"💳 Сохраненная карта: {'✅ ЕСТЬ' if saved_method else '❌ НЕТ'}")
    print(f"📊 Попыток автопродления: {attempts or 0}")
    
    if expires_at:
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            is_expired = expires_dt <= now
            print(f"⏰ Подписка истекает: {expires_at[:19]}")
            print(f"📌 Статус подписки: {'❌ ИСТЕКЛА' if is_expired else '✅ АКТИВНА'}")
        except:
            print(f"⏰ Подписка истекает: {expires_at}")
    else:
        print("⏰ Подписка: НЕТ")
    
    if starts_at:
        print(f"📅 Подписка началась: {starts_at[:19]}")
    
    # Проверяем ссылки
    cursor.execute("""
        SELECT invite_link, created_at, revoked
        FROM invite_links
        WHERE telegram_user_id = ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (telegram_id,))
    
    link = cursor.fetchone()
    if link:
        invite_link, link_created, revoked = link
        print(f"🔗 Последняя ссылка: {'❌ ОТОЗВАНА' if revoked else '✅ АКТИВНА'}")
        print(f"   Создана: {link_created[:19] if link_created else 'N/A'}")
    else:
        print("🔗 Ссылка-приглашение: НЕТ")
    
    print("\n=== ВАРИАНТЫ ВОССТАНОВЛЕНИЯ ДОСТУПА ===\n")
    print("1. РАЗБАНИТЬ в канале (unban_chat_member)")
    print("2. ВКЛЮЧИТЬ автопродление в БД")
    print("3. ПРОДЛИТЬ подписку (активировать на 30 дней)")
    print("4. СОЗДАТЬ новую ссылку-приглашение")
    print("5. ОДОБРИТЬ заявку на вступление (если пользователь подаст)")
    
else:
    print("❌ Пользователь не найден в базе данных")

conn.close()
