#!/usr/bin/env python3
"""
Скрипт для просмотра содержимого базы данных
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "/opt/bot_telegram/bot.db")

def show_database():
    """Показывает содержимое базы данных"""
    if not os.path.exists(DB_PATH):
        print(f"❌ База данных не найдена: {DB_PATH}")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("=" * 80)
    print("📊 СОДЕРЖИМОЕ БАЗЫ ДАННЫХ")
    print("=" * 80)
    print()
    
    # Список таблиц
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = cursor.fetchall()
    print(f"📋 Найдено таблиц: {len(tables)}")
    for table in tables:
        print(f"  • {table[0]}")
    print()
    
    # Таблица users
    print("=" * 80)
    print("👥 ТАБЛИЦА: users")
    print("=" * 80)
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    print(f"Всего пользователей: {count}")
    if count > 0:
        cursor.execute("SELECT telegram_id, username, created_at FROM users ORDER BY created_at DESC LIMIT 10")
        users = cursor.fetchall()
        print("\nПоследние 10 пользователей:")
        print(f"{'ID':<15} {'Username':<30} {'Дата создания':<25}")
        print("-" * 80)
        for user in users:
            telegram_id, username, created_at = user
            print(f"{telegram_id:<15} {username or 'N/A':<30} {created_at or 'N/A':<25}")
    print()
    
    # Таблица subscriptions
    print("=" * 80)
    print("📅 ТАБЛИЦА: subscriptions")
    print("=" * 80)
    cursor.execute("SELECT COUNT(*) FROM subscriptions")
    count = cursor.fetchone()[0]
    print(f"Всего подписок: {count}")
    if count > 0:
        cursor.execute("""
            SELECT telegram_id, starts_at, expires_at, auto_renewal_enabled 
            FROM subscriptions 
            ORDER BY expires_at DESC 
            LIMIT 10
        """)
        subs = cursor.fetchall()
        print("\nПоследние 10 подписок:")
        print(f"{'ID':<15} {'Начало':<25} {'Окончание':<25} {'Автопродление':<15}")
        print("-" * 80)
        for sub in subs:
            telegram_id, starts_at, expires_at, auto_renewal = sub
            auto_text = "✅ Да" if auto_renewal else "❌ Нет"
            print(f"{telegram_id:<15} {starts_at or 'N/A':<25} {expires_at or 'N/A':<25} {auto_text:<15}")
    print()
    
    # Таблица payments
    print("=" * 80)
    print("💳 ТАБЛИЦА: payments")
    print("=" * 80)
    cursor.execute("SELECT COUNT(*) FROM payments")
    count = cursor.fetchone()[0]
    print(f"Всего платежей: {count}")
    if count > 0:
        cursor.execute("""
            SELECT telegram_id, payment_id, status, created_at 
            FROM payments 
            ORDER BY created_at DESC 
            LIMIT 10
        """)
        payments = cursor.fetchall()
        print("\nПоследние 10 платежей:")
        print(f"{'ID':<15} {'Payment ID':<30} {'Статус':<15} {'Дата':<25}")
        print("-" * 80)
        for payment in payments:
            telegram_id, payment_id, status, created_at = payment
            print(f"{telegram_id:<15} {payment_id[:28]:<30} {status:<15} {created_at or 'N/A':<25}")
    print()
    
    # Таблица bonus_week_config
    print("=" * 80)
    print("🎁 ТАБЛИЦА: bonus_week_config")
    print("=" * 80)
    cursor.execute("SELECT * FROM bonus_week_config")
    config = cursor.fetchone()
    if config:
        print(f"ID: {config[0]}")
        print(f"Время начала: {config[1]}")
        print(f"Обновлено: {config[2]}")
    else:
        print("Конфигурация бонусной недели не найдена")
    print()
    
    # Проверка дат окончания подписок
    print("=" * 80)
    print("🔍 ПРОВЕРКА ДАТ ОКОНЧАНИЯ ПОДПИСОК")
    print("=" * 80)
    cursor.execute("""
        SELECT telegram_id, expires_at 
        FROM subscriptions 
        WHERE expires_at IS NOT NULL
        ORDER BY expires_at
    """)
    expires = cursor.fetchall()
    print(f"Всего подписок с датой окончания: {len(expires)}")
    if expires:
        print("\nДаты окончания подписок:")
        print(f"{'ID':<15} {'Дата окончания':<30}")
        print("-" * 80)
        unique_dates = {}
        for telegram_id, expires_at in expires:
            if expires_at:
                if expires_at not in unique_dates:
                    unique_dates[expires_at] = []
                unique_dates[expires_at].append(telegram_id)
        
        for date, ids in sorted(unique_dates.items()):
            print(f"{date:<30} ({len(ids)} пользователей)")
    print()
    
    conn.close()
    print("=" * 80)

if __name__ == "__main__":
    show_database()
