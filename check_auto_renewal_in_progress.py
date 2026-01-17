#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone, timedelta

conn = sqlite3.connect('/opt/bot_telegram/bot.db')
cursor = conn.cursor()

print("=== ПОЛЬЗОВАТЕЛИ С АВТОПРОДЛЕНИЕМ В ПРОЦЕССЕ ===\n")

# Получаем всех пользователей с автопродлением
cursor.execute("""
    SELECT 
        s.telegram_id,
        u.username,
        s.auto_renewal_enabled,
        s.auto_renewal_attempts,
        s.last_auto_renewal_attempt_at,
        s.expires_at,
        s.saved_payment_method_id
    FROM subscriptions s
    LEFT JOIN users u ON s.telegram_id = u.telegram_id
    WHERE s.auto_renewal_enabled = 1
    ORDER BY s.auto_renewal_attempts DESC, s.telegram_id
""")

subs = cursor.fetchall()

# Группируем по статусу
in_progress = []  # 0 < attempts < 3
successful = []   # attempts = 0 и подписка продлена
failed = []       # attempts >= 3
not_started = []  # attempts = 0 но подписка еще не истекла

now = datetime.now(timezone.utc)

for sub in subs:
    telegram_id, username, auto_renewal, attempts, last_attempt_at, expires_at, saved_method = sub
    attempts = attempts or 0
    
    # Проверяем статус подписки
    if expires_at:
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            is_expired = expires_dt <= now
            is_extended = expires_dt > now + timedelta(days=20)  # Продлена на месяц
        except:
            is_expired = True
            is_extended = False
    else:
        is_expired = True
        is_extended = False
    
    # Определяем статус
    if attempts >= 3:
        failed.append((sub, is_expired))
    elif attempts == 0 and is_extended:
        successful.append(sub)
    elif 0 < attempts < 3:
        in_progress.append((sub, is_expired))
    else:
        not_started.append(sub)

# Показываем тех, у кого идут попытки
if in_progress:
    print(f"🔄 АВТОПРОДЛЕНИЕ В ПРОЦЕССЕ: {len(in_progress)} пользователей\n")
    
    for sub, is_expired in in_progress:
        telegram_id, username, auto_renewal, attempts, last_attempt_at, expires_at, saved_method = sub
        attempts = attempts or 0
        
        print(f"👤 ID: {telegram_id}")
        print(f"   Username: {username or 'N/A'}")
        print(f"   Попыток: {attempts} из 3")
        
        if last_attempt_at:
            try:
                last_attempt_dt = datetime.fromisoformat(last_attempt_at.replace('Z', '+00:00'))
                if last_attempt_dt.tzinfo is None:
                    last_attempt_dt = last_attempt_dt.replace(tzinfo=timezone.utc)
                
                time_since = (now - last_attempt_dt).total_seconds() / 60
                hours_since = int(time_since // 60)
                minutes_since = int(time_since % 60)
                
                print(f"   Последняя попытка: {last_attempt_dt.strftime('%H:%M:%S')} ({hours_since}ч {minutes_since}м назад)")
                
                # Следующая попытка через 2 часа (120 минут)
                AUTO_RENEWAL_ATTEMPT_INTERVAL_MINUTES = 120
                next_attempt = last_attempt_dt + timedelta(minutes=AUTO_RENEWAL_ATTEMPT_INTERVAL_MINUTES)
                if next_attempt > now:
                    time_until = (next_attempt - now).total_seconds() / 60
                    hours_until = int(time_until // 60)
                    minutes_until = int(time_until % 60)
                    print(f"   Следующая попытка: через {hours_until}ч {minutes_until}м (в {next_attempt.strftime('%H:%M:%S')})")
                else:
                    print(f"   Следующая попытка: СЕЙЧАС (прошло более 2 часов)")
            except Exception as e:
                print(f"   Последняя попытка: {last_attempt_at}")
        else:
            print(f"   Последняя попытка: нет данных")
        
        print(f"   Статус подписки: {'❌ Истекла' if is_expired else '✅ Активна'}")
        if expires_at:
            expires_short = expires_at[:16] if expires_at else "N/A"
            print(f"   Истекает: {expires_short}")
        print()
else:
    print("✅ Нет пользователей с автопродлением в процессе\n")

# Показываем успешные
if successful:
    print(f"✅ УСПЕШНО ПРОДЛЕНО: {len(successful)} пользователей\n")
    for sub in successful[:5]:
        telegram_id, username, auto_renewal, attempts, last_attempt_at, expires_at, saved_method = sub
        expires_short = expires_at[:16] if expires_at else "N/A"
        print(f"  - ID: {telegram_id}, Username: {username or 'N/A'}, Истекает: {expires_short}")
    if len(successful) > 5:
        print(f"  ... и еще {len(successful) - 5} пользователей")
    print()

# Показываем неудачные (3 попытки)
if failed:
    print(f"❌ НЕУДАЧНЫЕ (3 попытки): {len(failed)} пользователей\n")
    for sub, is_expired in failed[:5]:
        telegram_id, username, auto_renewal, attempts, last_attempt_at, expires_at, saved_method = sub
        print(f"  - ID: {telegram_id}, Username: {username or 'N/A'}")
    if len(failed) > 5:
        print(f"  ... и еще {len(failed) - 5} пользователей")
    print()

conn.close()
