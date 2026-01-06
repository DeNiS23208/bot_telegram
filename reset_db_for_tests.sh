#!/bin/bash
# Скрипт для быстрой очистки БД для тестов

echo "🔄 Очистка базы данных для тестов..."

# Используем Python из venv для очистки базы данных
source venv/bin/activate 2>/dev/null || true

python3 << 'PYTHON_SCRIPT'
import aiosqlite
import asyncio
import os

async def reset_db():
    db_path = "bot.db"
    if not os.path.exists(db_path):
        print("⚠️ База данных не найдена, создается новая...")
        return
    
    async with aiosqlite.connect(db_path) as db:
        # Очищаем все таблицы
        tables = [
            "users",
            "subscriptions", 
            "payments",
            "form_data",
            "invite_links",
            "approved_users",
            "processed_payments"
        ]
        
        for table in tables:
            try:
                await db.execute(f"DELETE FROM {table}")
                print(f"✅ Очищена таблица: {table}")
            except Exception as e:
                # Игнорируем ошибки если таблицы не существует
                pass
        
        await db.commit()
        print("✅ Готово! База данных очищена.")

asyncio.run(reset_db())
PYTHON_SCRIPT

echo "✅ Готово! База данных очищена."
