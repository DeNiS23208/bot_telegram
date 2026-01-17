#!/usr/bin/env python3
import sqlite3
import sys

DB_PATH = "/opt/bot_telegram/bot.db"

try:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("Очистка базы данных...")
    
    # Удаляем данные из всех таблиц
    tables = [
        "invite_links",
        "processed_payments", 
        "subscriptions",
        "payments",
        "approved_users",
        "users"
    ]
    
    for table in tables:
        try:
            cur.execute(f"DELETE FROM {table}")
            count = cur.rowcount
            print(f"✅ Очищена таблица {table}: {count} записей")
        except Exception as e:
            print(f"⚠️ Ошибка очистки {table}: {e}")
    
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    
    print("✅ База данных полностью очищена!")
    
except Exception as e:
    print(f"❌ Ошибка: {e}")
    sys.exit(1)

