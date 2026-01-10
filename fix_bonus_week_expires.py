#!/usr/bin/env python3
"""
Скрипт для исправления дат окончания подписок в БД
Устанавливает одинаковую дату окончания (14.01.2026 10:58:42 UTC) для всех подписок, созданных во время бонусной недели
"""
import os
import sqlite3
from datetime import datetime, timezone

# Пытаемся прочитать DB_PATH из переменных окружения или используем стандартный путь
DB_PATH = os.getenv("DB_PATH", "/opt/bot_telegram/bot.db")
BONUS_WEEK_END = datetime(2026, 1, 14, 10, 58, 42, tzinfo=timezone.utc)

def fix_bonus_week_expires():
    """Исправляет даты окончания подписок для бонусной недели"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Получаем все подписки
    cursor.execute("SELECT telegram_id, expires_at, starts_at FROM subscriptions")
    rows = cursor.fetchall()
    
    print(f"Найдено подписок: {len(rows)}")
    print(f"Целевая дата окончания: {BONUS_WEEK_END} (14.01.2026 10:58:42 UTC)")
    print()
    
    fixed_count = 0
    for telegram_id, expires_at_str, starts_at_str in rows:
        if not expires_at_str:
            continue
            
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            
            # Проверяем, нужно ли исправить (если дата окончания раньше целевой даты)
            # Это означает, что подписка была создана во время бонусной недели
            if expires_at < BONUS_WEEK_END:
                # Обновляем дату окончания на фиксированную
                cursor.execute(
                    "UPDATE subscriptions SET expires_at = ? WHERE telegram_id = ?",
                    (BONUS_WEEK_END.isoformat(), telegram_id)
                )
                fixed_count += 1
                print(f"✅ Исправлено для {telegram_id}: {expires_at} -> {BONUS_WEEK_END}")
        except Exception as e:
            print(f"⚠️ Ошибка для {telegram_id}: {e}")
    
    conn.commit()
    conn.close()
    
    print()
    print(f"✅ Исправлено подписок: {fixed_count}")

if __name__ == "__main__":
    fix_bonus_week_expires()
