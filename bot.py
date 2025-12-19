import asyncio
import os
import inspect
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

from db import (
    init_db,
    ensure_user,
    get_subscription_expires_at,
    activate_subscription_days,
    save_payment,
    update_payment_status,
    get_latest_payment_id,
)
from payments import create_payment, get_payment_status

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
RETURN_URL = os.getenv("YOOKASSA_RETURN_URL", "https://xasanim.ru/")
CUSTOMER_EMAIL = os.getenv("PAYMENT_CUSTOMER_EMAIL", "test@example.com")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Тексты кнопок (держим в одном месте)
BTN_PAY_1 = "💳 Оплатить доступ"
BTN_PAY_2 = "Оплатить доступ"
BTN_PAY_3 = "Оплатить подписку"

BTN_STATUS_1 = "📌 Статус подписки"
BTN_STATUS_2 = "Статус подписки"

BTN_ABOUT_1 = "ℹ️ О проекте"
BTN_ABOUT_2 = "О проекте"

BTN_CHECK_1 = "✅ Проверить оплату"
BTN_CHECK_2 = "Проверить оплату"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PAY_1)],
            [KeyboardButton(text=BTN_STATUS_1)],
            [KeyboardButton(text=BTN_ABOUT_1)],
            [KeyboardButton(text=BTN_CHECK_1)],
        ],
        resize_keyboard=True,
    )


async def maybe_await(func, *args, **kwargs):
    """
    Позволяет вызывать функцию, которая может быть sync или async.
    Если func возвращает корутину - await'им её, иначе возвращаем как есть.
    """
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "Привет! Это тестовый бот для MVP.\nВыбери действие:",
        reply_markup=main_menu(),
    )


@dp.message(lambda m: (m.text or "").strip() in {BTN_STATUS_1, BTN_STATUS_2})
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


@dp.message(lambda m: (m.text or "").strip() in {BTN_ABOUT_1, BTN_ABOUT_2})
async def about(message: Message):
    await message.answer(
        "Это MVP Telegram-бота с доступом в закрытый канал.\n"
        "Оплата через ЮKassa + подписка в SQLite."
    )


@dp.message(lambda m: (m.text or "").strip() in {BTN_PAY_1, BTN_PAY_2, BTN_PAY_3})
async def pay(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username)

    # Чтобы больше не было "нажал и тишина", выводим ошибку пользователю,
    # а в логах пусть валится дальше со stack trace.
    try:
        # create_payment может быть sync или async, поэтому вызываем безопасно
        payment_id, pay_url = await maybe_await(
            create_payment,
            amount_rub="10.00",
            description="Подписка на канал (30 дней)",
            return_url=RETURN_URL,
            customer_email=CUSTOMER_EMAIL,  # ✅ ВАЖНО для receipt (54-ФЗ)
        )
    except Exception as e:
        await message.answer(f"Ошибка при создании платежа: {type(e).__name__}: {e}")
        raise

    await save_payment(message.from_user.id, payment_id, status="pending")

    await message.answer(
        "Чтобы оплатить, перейдите по ссылке:\n"
        f"{pay_url}\n\n"
        "После оплаты вернитесь сюда и нажмите: ✅ Проверить оплату"
    )


@dp.message(lambda m: (m.text or "").strip() in {BTN_CHECK_1, BTN_CHECK_2})
async def check_payment(message: Message):
    payment_id = await get_latest_payment_id(message.from_user.id)

    if not payment_id:
        await message.answer("Не нашёл платежей. Сначала нажмите 💳 Оплатить доступ.")
        return

    # get_payment_status может быть sync или async
    try:
        status = await maybe_await(get_payment_status, payment_id)
    except Exception as e:
        await message.answer(f"Ошибка при проверке платежа: {type(e).__name__}: {e}")
        raise

    await update_payment_status(payment_id, status)

    if status == "succeeded":
        expires_at = await activate_subscription_days(message.from_user.id, days=30)
        await message.answer(
            f"✅ Оплата подтверждена.\nПодписка активна до: {expires_at.date()}"
        )
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

