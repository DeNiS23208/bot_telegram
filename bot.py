import asyncio
import os
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
from db import init_db, ensure_user, get_subscription_expires_at, activate_subscription_days
from payments import create_payment

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
RETURN_URL = os.getenv("YOOKASSA_RETURN_URL", "https://example.com/return")

bot = Bot(token=TOKEN)
dp = Dispatcher()


def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Оплатить доступ")],
            [KeyboardButton(text="📌 Статус подписки")],
            [KeyboardButton(text="ℹ️ О проекте")]
        ],
        resize_keyboard=True
    )


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "Привет! Это тестовый бот для MVP.\nВыбери действие:",
        reply_markup=main_menu()
    )


@dp.message(lambda message: message.text == "📌 Статус подписки")
async def sub_status(message: Message):
    expires_at = await get_subscription_expires_at(message.from_user.id)

    if not expires_at:
        await message.answer("Подписка не активна ❌")
        return

    now = datetime.utcnow()
    if expires_at > now:
        await message.answer(f"Подписка активна ✅\nДействует до: {expires_at.date()}")
    else:
        await message.answer(f"Подписка закончилась ❌\nЗакончилась: {expires_at.date()}")


@dp.message(lambda message: message.text == "ℹ️ О проекте")
async def about(message: Message):
    await message.answer(
        "Это MVP Telegram-бота с доступом в закрытый канал.\n"
        "Сегодня мы подключили базу данных (SQLite) и статус подписки."
    )


@dp.message(lambda message: message.text == "💳 Оплатить доступ")
async def pay(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username)

    payment_id, pay_url = create_payment(
        amount_rub="299.00",
        description=f"Подписка на канал. tg_id={message.from_user.id}",
        return_url=RETURN_URL
    )

    await message.answer(
        "Чтобы оплатить, перейдите по ссылке:\n"
        f"{pay_url}\n\n"
        f"ID платежа: {payment_id}"
    )


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
