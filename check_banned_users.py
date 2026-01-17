#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect('/opt/bot_telegram/bot.db')
cursor = conn.cursor()

print("=== ПОЛЬЗОВАТЕЛИ БЕЗ АВТОПРОДЛЕНИЯ (ЗАБАНЕНЫ) ===\n")

# Получаем пользователей без автопродления
cursor.execute("""
    SELECT 
        s.telegram_id,
        u.username,
        s.auto_renewal_enabled,
        s.expires_at,
        s.starts_at
    FROM subscriptions s
    LEFT JOIN users u ON s.telegram_id = u.telegram_id
    WHERE s.auto_renewal_enabled = 0
    ORDER BY s.telegram_id
""")

subs = cursor.fetchall()

now = datetime.now(timezone.utc)
bonus_week_end = datetime(2026, 1, 14, 10, 58, 42, tzinfo=timezone.utc)

banned_users = []

for sub in subs:
    telegram_id, username, auto_renewal, expires_at, starts_at = sub
    
    # Проверяем, была ли это бонусная подписка
    is_bonus = False
    if starts_at:
        try:
            starts_dt = datetime.fromisoformat(starts_at.replace('Z', '+00:00'))
            if starts_dt.tzinfo is None:
                starts_dt = starts_dt.replace(tzinfo=timezone.utc)
            is_bonus = starts_dt <= bonus_week_end
        except:
            pass
    
    # Проверяем, истекла ли подписка
    is_expired = False
    if expires_at:
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            is_expired = expires_dt <= now
        except:
            pass
    
    # Если это была бонусная подписка и она истекла - пользователь был забанен
    if is_bonus and is_expired:
        banned_users.append(sub)

print(f"🚫 ЗАБАНЕНО ПОСЛЕ ОКОНЧАНИЯ БОНУСНОЙ НЕДЕЛИ: {len(banned_users)} пользователей\n")

if banned_users:
    for sub in banned_users:
        telegram_id, username, auto_renewal, expires_at, starts_at = sub
        expires_short = expires_at[:16] if expires_at else "N/A"
        starts_short = starts_at[:16] if starts_at else "N/A"
        
        print(f"👤 ID: {telegram_id}")
        print(f"   Username: {username or 'N/A'}")
        print(f"   Подписка началась: {starts_short}")
        print(f"   Подписка истекла: {expires_short}")
        print(f"   Автопродление: ❌ ОТКЛЮЧЕНО")
        print()
else:
    print("✅ Нет забаненных пользователей без автопродления\n")

# Также показываем всех без автопродления (для справки)
print(f"\n📊 ВСЕГО БЕЗ АВТОПРОДЛЕНИЯ: {len(subs)} пользователей\n")
for sub in subs:
    telegram_id, username, auto_renewal, expires_at, starts_at = sub
    expires_short = expires_at[:16] if expires_at else "N/A"
    print(f"  - ID: {telegram_id}, Username: {username or 'N/A'}, Истекает: {expires_short}")

conn.close()
