#!/usr/bin/env python3
"""
Скрипт для разбана аккаунта поддержки otd_zabota (ID: 8429417659)
"""
import os
import asyncio
from dotenv import load_dotenv
from aiogram import Bot

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
SUPPORT_USER_ID = 8429417659  # otd_zabota

async def unban_support_account():
    """Разбанивает аккаунт поддержки в канале"""
    bot = Bot(token=BOT_TOKEN)
    
    try:
        print(f"🔄 Разбан аккаунта поддержки (ID: {SUPPORT_USER_ID})...")
        
        # Разбаниваем пользователя
        await bot.unban_chat_member(
            chat_id=CHANNEL_ID,
            user_id=SUPPORT_USER_ID,
            only_if_banned=True  # Разбанить только если забанен
        )
        
        print(f"✅ Аккаунт {SUPPORT_USER_ID} успешно разбанен в канале!")
        
        # Проверяем статус пользователя в канале
        try:
            chat_member = await bot.get_chat_member(
                chat_id=CHANNEL_ID,
                user_id=SUPPORT_USER_ID
            )
            print(f"📊 Статус пользователя в канале: {chat_member.status}")
            
            if chat_member.status in ['member', 'administrator', 'creator']:
                print("✅ Пользователь теперь в канале!")
            elif chat_member.status == 'left':
                print("ℹ️ Пользователь не в канале (нужно добавить вручную)")
            elif chat_member.status == 'kicked':
                print("⚠️ Пользователь все еще забанен (возможно нужны права администратора)")
        except Exception as check_error:
            print(f"⚠️ Не удалось проверить статус: {check_error}")
        
    except Exception as e:
        print(f"❌ Ошибка при разбане: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(unban_support_account())
