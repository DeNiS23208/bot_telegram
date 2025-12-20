#!/usr/bin/env python3
"""
Скрипт для очистки базы данных от старых записей
Удаляет записи о пользователях, которые были в канале ранее
"""
import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "bot.db")

def clear_old_data():
    """Очищает старые данные из БД"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("Очистка базы данных...")
    
    # Очищаем таблицу approved_users (можно закомментировать, если не нужно)
    # cur.execute("DELETE FROM approved_users")
    # print("✅ Очищена таблица approved_users")
    
    # Очищаем таблицу invite_links
    cur.execute("DELETE FROM invite_links")
    print("✅ Очищена таблица invite_links")
    
    # Очищаем таблицу processed_payments (можно закомментировать)
    # cur.execute("DELETE FROM processed_payments")
    # print("✅ Очищена таблица processed_payments")
    
    conn.commit()
    conn.close()
    
    print("\n✅ Очистка завершена!")
    print("\n⚠️ ВНИМАНИЕ: Если вы очистили approved_users, пользователям нужно будет оплатить заново.")

if __name__ == "__main__":
    response = input("Вы уверены, что хотите очистить базу данных? (yes/no): ")
    if response.lower() == "yes":
        clear_old_data()
    else:
        print("Отменено.")

