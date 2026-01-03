#!/usr/bin/env python3
"""
Скрипт для полной очистки базы данных
⚠️ ВНИМАНИЕ: Удалит ВСЕ данные из базы данных!
"""
import os
import sqlite3

# Пытаемся загрузить .env, но не падаем если его нет
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.getenv("DB_PATH", "/opt/bot_telegram/bot.db")

def reset_database():
    """Полностью очищает базу данных"""
    if not os.path.exists(DB_PATH):
        print(f"❌ База данных не найдена: {DB_PATH}")
        return
    
    # Резервные копии не создаются по запросу пользователя
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("🗑️ Очистка базы данных...")
    print(f"База данных: {DB_PATH}\n")
    
    # Получаем количество записей в каждой таблице
    tables = [
        "users",
        "subscriptions", 
        "payments",
        "approved_users",
        "invite_links",
        "processed_payments"
    ]
    
    print("Текущее состояние БД:")
    for table in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            print(f"  - {table}: {count} записей")
        except sqlite3.OperationalError:
            print(f"  - {table}: таблица не существует")
    
    print("\n🗑️ Удаление всех данных...")
    
    # Удаляем данные из всех таблиц
    for table in tables:
        try:
            cur.execute(f"DELETE FROM {table}")
            print(f"✅ Очищена таблица {table}")
        except sqlite3.OperationalError:
            print(f"⚠️ Таблица {table} не существует, пропускаем")
    
    conn.commit()
    conn.close()
    
    print("\n✅ База данных полностью очищена!")
    print("\n⚠️ ВНИМАНИЕ: Все данные удалены!")
    print("   При следующем запуске бота таблицы будут созданы заново.")

if __name__ == "__main__":
    print("⚠️ ВНИМАНИЕ: Этот скрипт удалит ВСЕ данные из базы данных!")
    print(f"База данных: {DB_PATH}\n")
    response = input("Вы уверены, что хотите полностью очистить базу данных? (yes/no): ")
    if response.lower() == "yes":
        reset_database()
    else:
        print("Отменено.")






