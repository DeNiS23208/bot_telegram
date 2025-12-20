#!/usr/bin/env python3
"""
Скрипт для очистки базы данных от старых записей
Удаляет записи о пользователях, которые были в канале ранее
"""
import os
import sqlite3

# Пытаемся загрузить .env, но не падаем если его нет
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Если dotenv не установлен, используем переменные окружения напрямую

DB_PATH = os.getenv("DB_PATH", "/opt/bot_telegram/bot.db")

def clear_old_data():
    """Очищает старые данные из БД"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("Очистка базы данных...")
    print(f"База данных: {DB_PATH}\n")
    
    # Сначала создаем таблицы, если их нет (как в webhook_app.py)
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
    conn.commit()
    print("✅ Таблицы проверены/созданы\n")
    
    # Проверяем наличие данных перед очисткой
    cur.execute("SELECT COUNT(*) FROM invite_links")
    invite_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM approved_users")
    approved_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM processed_payments")
    payments_count = cur.fetchone()[0]
    
    print(f"Текущее состояние БД:")
    print(f"  - invite_links: {invite_count} записей")
    print(f"  - approved_users: {approved_count} записей")
    print(f"  - processed_payments: {payments_count} записей\n")
    
    # Очищаем таблицу invite_links
    if invite_count > 0:
        cur.execute("DELETE FROM invite_links")
        print(f"✅ Очищена таблица invite_links ({invite_count} записей)")
    else:
        print("ℹ️ Таблица invite_links уже пуста")
    
    # Очищаем таблицу processed_payments (можно закомментировать, если не нужно)
    # if payments_count > 0:
    #     cur.execute("DELETE FROM processed_payments")
    #     print(f"✅ Очищена таблица processed_payments ({payments_count} записей)")
    # else:
    #     print("ℹ️ Таблица processed_payments уже пуста")
    
    # Очищаем таблицу approved_users (можно закомментировать, если не нужно)
    # ⚠️ ВНИМАНИЕ: Если очистить approved_users, пользователям нужно будет оплатить заново!
    # if approved_count > 0:
    #     cur.execute("DELETE FROM approved_users")
    #     print(f"✅ Очищена таблица approved_users ({approved_count} записей)")
    # else:
    #     print("ℹ️ Таблица approved_users уже пуста")
    
    conn.commit()
    conn.close()
    
    print("\n✅ Очистка завершена!")
    print("\n⚠️ ВНИМАНИЕ: Таблица approved_users НЕ очищена (закомментировано).")
    print("   Если нужно очистить список оплативших пользователей, раскомментируйте соответствующие строки.")

if __name__ == "__main__":
    response = input("Вы уверены, что хотите очистить базу данных? (yes/no): ")
    if response.lower() == "yes":
        clear_old_data()
    else:
        print("Отменено.")

