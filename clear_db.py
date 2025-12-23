#!/usr/bin/env python3
"""
Скрипт для очистки базы данных от старых записей
Удаляет записи о пользователях, которые были в канале ранее

Для полной очистки БД (для тестов) используйте флаг --full
"""
import os
import sqlite3
import sys

# Пытаемся загрузить .env, но не падаем если его нет
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Если dotenv не установлен, используем переменные окружения напрямую

DB_PATH = os.getenv("DB_PATH", "/opt/bot_telegram/bot.db")

# Проверка флага --full для полной очистки
FULL_CLEAR = "--full" in sys.argv or "-f" in sys.argv

def clear_old_data():
    """Очищает старые данные из БД"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("Очистка базы данных...")
    print(f"База данных: {DB_PATH}\n")
    
    # Сначала создаем таблицы, если их нет (как в webhook_app.py и db.py)
    print("Создание таблиц, если их нет...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_payments (
            payment_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS approved_users (
            telegram_user_id INTEGER PRIMARY KEY,
            approved_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS invite_links (
            invite_link TEXT PRIMARY KEY,
            telegram_user_id INTEGER NOT NULL,
            payment_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked INTEGER DEFAULT 0,
            FOREIGN KEY (telegram_user_id) REFERENCES approved_users(telegram_user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            telegram_id INTEGER PRIMARY KEY,
            expires_at TEXT,
            starts_at TEXT,
            auto_renewal_enabled INTEGER DEFAULT 0,
            saved_payment_method_id TEXT,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            payment_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    print("✅ Таблицы проверены/созданы\n")
    
    # Проверяем наличие данных перед очисткой
    cur.execute("SELECT COUNT(*) FROM invite_links")
    invite_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM approved_users")
    approved_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM processed_payments")
    payments_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM subscriptions")
    subscriptions_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM payments")
    payments_table_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM users")
    users_count = cur.fetchone()[0]
    
    print(f"Текущее состояние БД:")
    print(f"  - invite_links: {invite_count} записей")
    print(f"  - approved_users: {approved_count} записей")
    print(f"  - processed_payments: {payments_count} записей")
    print(f"  - subscriptions: {subscriptions_count} записей")
    print(f"  - payments: {payments_table_count} записей")
    print(f"  - users: {users_count} записей\n")
    
    if FULL_CLEAR:
        print("⚠️ РЕЖИМ ПОЛНОЙ ОЧИСТКИ (для тестов)")
        print("   Будут удалены ВСЕ данные: подписки, платежи, пользователи\n")
    else:
        print("ℹ️ Обычный режим очистки (только invite_links)")
        print("   Для полной очистки используйте: python3 clear_db.py --full\n")
    
    # Очищаем таблицу invite_links (всегда)
    if invite_count > 0:
        cur.execute("DELETE FROM invite_links")
        print(f"✅ Очищена таблица invite_links ({invite_count} записей)")
    else:
        print("ℹ️ Таблица invite_links уже пуста")
    
    # Полная очистка для тестов
    if FULL_CLEAR:
        # Очищаем подписки
        if subscriptions_count > 0:
            cur.execute("DELETE FROM subscriptions")
            print(f"✅ Очищена таблица subscriptions ({subscriptions_count} записей)")
        
        # Очищаем платежи
        if payments_table_count > 0:
            cur.execute("DELETE FROM payments")
            print(f"✅ Очищена таблица payments ({payments_table_count} записей)")
        
        # Очищаем approved_users
        if approved_count > 0:
            cur.execute("DELETE FROM approved_users")
            print(f"✅ Очищена таблица approved_users ({approved_count} записей)")
        
        # Очищаем processed_payments
        if payments_count > 0:
            cur.execute("DELETE FROM processed_payments")
            print(f"✅ Очищена таблица processed_payments ({payments_count} записей)")
        
        # Очищаем users (опционально, можно оставить для истории)
        # if users_count > 0:
        #     cur.execute("DELETE FROM users")
        #     print(f"✅ Очищена таблица users ({users_count} записей)")
    
    conn.commit()
    conn.close()
    
    if FULL_CLEAR:
        print("\n✅ Полная очистка завершена! БД готова для тестов.")
    else:
        print("\n✅ Очистка завершена!")
        print("\n💡 Для полной очистки (включая подписки и платежи) используйте:")
        print("   python3 clear_db.py --full")

if __name__ == "__main__":
    # Проверка флага --yes для автоматического подтверждения
    AUTO_YES = "--yes" in sys.argv or "-y" in sys.argv
    
    if FULL_CLEAR:
        print("⚠️ ВНИМАНИЕ: Будет выполнена ПОЛНАЯ очистка базы данных!")
        print("   Это удалит все подписки, платежи и данные пользователей.")
        if not AUTO_YES:
            response = input("Вы уверены? (yes/no): ")
        else:
            response = "yes"
            print("✅ Автоматическое подтверждение (--yes)")
    else:
        print("ℹ️ Будет очищена только таблица invite_links.")
        if not AUTO_YES:
            response = input("Продолжить? (yes/no): ")
        else:
            response = "yes"
            print("✅ Автоматическое подтверждение (--yes)")
    
    if response.lower() == "yes":
        clear_old_data()
    else:
        print("Отменено.")

