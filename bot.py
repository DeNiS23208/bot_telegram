import asyncio
import os
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
from db import init_db, ensure_user, get_subscription_expires_at, activate_subscription_days
from payments import create_payment
from db import save_payment, update_payment_status, get_latest_payment_id, activate_subscription_days
from payments import create_payment, get_payment_status

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
            [KeyboardButton(text="ℹ️ О проекте")],
            [KeyboardButton(text="✅ Проверить оплату")]
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
        amount_rub="10.00",
        description=f"Подписка на канал. tg_id={message.from_user.id}",
        return_url=RETURN_URL
    )

    await save_payment(message.from_user.id, payment_id, status="pending")

    await message.answer(
        "Чтобы оплатить, перейдите по ссылке:\n"
        f"{pay_url}\n\n"
        "После оплаты вернитесь сюда и нажмите: ✅ Проверить оплату"
    )


@dp.message(lambda message: message.text == "✅ Проверить оплату")
async def check_payment(message: Message):
    payment_id = await get_latest_payment_id(message.from_user.id)

    if not payment_id:
        await message.answer("Не нашёл платежей. Сначала нажмите 💳 Оплатить доступ.")
        return

    status = get_payment_status(payment_id)
    await update_payment_status(payment_id, status)

    if status == "succeeded":
        expires_at = await activate_subscription_days(message.from_user.id, days=30)
        await message.answer(f"✅ Оплата подтверждена.\nПодписка активна до: {expires_at.date()}")
    elif status in ("pending", "waiting_for_capture"):
        await message.answer("Платёж пока не завершён. Попробуйте ещё раз через минуту.")
    elif status == "canceled":
        await message.answer("Платёж отменён/не оплачен ❌")
    else:
        await message.answer(f"Статус платежа: {status}")


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
