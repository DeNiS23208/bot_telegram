import asyncio
import os
import inspect
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ChatJoinRequest
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
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# Для MVP можно фиксированный email, потом заменим на ввод пользователем
CUSTOMER_EMAIL = os.getenv("PAYMENT_CUSTOMER_EMAIL", "test@example.com")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Импортируем функцию проверки оплативших пользователей из webhook_app
# Для этого создадим простую функцию проверки в db.py или используем прямое подключение к БД
import sqlite3
DB_PATH = os.getenv("DB_PATH", "bot.db")

def is_user_allowed(tg_user_id: int) -> bool:
    """Проверяет, есть ли пользователь в списке оплативших"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM approved_users WHERE telegram_user_id = ?",
            (tg_user_id,)
        )
        row = cur.fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False

BTN_PAY_1 = "💳 Оплатить доступ на 30 дней"
BTN_STATUS_1 = "📌 Статус подписки"
BTN_ABOUT_1 = "ℹ️ О проекте"
BTN_CHECK_1 = "✅ Проверить оплату"


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


@dp.message(lambda m: (m.text or "").strip() == BTN_STATUS_1)
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


@dp.message(lambda m: (m.text or "").strip() == BTN_ABOUT_1)
async def about(message: Message):
    await message.answer(
        "Это MVP Telegram-бота с доступом в закрытый канал.\n"
        "Оплата через ЮKassa + подписка в SQLite."
    )


@dp.message(lambda m: (m.text or "").strip() == BTN_PAY_1)
async def pay(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username)

    payment_id, pay_url = await maybe_await(
        create_payment,
        amount_rub="10.00",
        description="Подписка на канал (30 дней)",
        return_url=RETURN_URL,
        customer_email=CUSTOMER_EMAIL,
        telegram_user_id=message.from_user.id,  # ✅ КРИТИЧНО
    )

    await save_payment(message.from_user.id, payment_id, status="pending")

    await message.answer(
        "Чтобы оплатить, перейдите по ссылке:\n"
        f"{pay_url}\n\n"
        "После оплаты вернитесь сюда и нажмите: ✅ Проверить оплату"
    )


@dp.message(lambda m: (m.text or "").strip() == BTN_CHECK_1)
async def check_payment(message: Message):
    payment_id = await get_latest_payment_id(message.from_user.id)

    if not payment_id:
        await message.answer("Не нашёл платежей. Сначала нажмите 💳 Оплатить доступ.")
        return

    status = await maybe_await(get_payment_status, payment_id)
    await update_payment_status(payment_id, status)

    if status == "succeeded":
        expires_at = await activate_subscription_days(message.from_user.id, days=30)
        await message.answer(
            f"✅ Оплата подтверждена!\n\n"
            f"Подписка активна до: {expires_at.date()}\n\n"
            f"Если вы ещё не получили ссылку на канал, она должна прийти в ближайшее время."
        )
    elif status in ("pending", "waiting_for_capture"):
        await message.answer(
            "⏳ Платёж пока не завершён\n\n"
            "Статус: ожидание оплаты\n\n"
            "Если вы уже оплатили, подождите несколько минут и нажмите эту кнопку снова.\n"
            "Если оплата не прошла, попробуйте оплатить заново."
        )
    elif status == "canceled":
        await message.answer(
            "❌ Платёж не был завершён\n\n"
            "Оплата была отменена или не прошла.\n\n"
            "Возможные причины:\n"
            "• Недостаточно средств на карте\n"
            "• Операция была отменена\n"
            "• Истекло время ожидания оплаты\n\n"
            "Вы можете попробовать оплатить снова, нажав кнопку 💳 Оплатить доступ."
        )
    else:
        await message.answer(
            f"ℹ️ Статус платежа: {status}\n\n"
            "Если оплата не прошла, попробуйте оплатить заново."
        )


@dp.chat_join_request()
async def approve_join_request(join_request: ChatJoinRequest):
    """
    Автоматически одобряет заявки на вступление от оплативших пользователей
    """
    if CHANNEL_ID and join_request.chat.id == CHANNEL_ID:
        user_id = join_request.from_user.id
        
        # Проверяем, оплатил ли пользователь
        if is_user_allowed(user_id):
            try:
                await join_request.approve()
                print(f"✅ Автоматически одобрена заявка от пользователя {user_id}")
            except Exception as e:
                print(f"❌ Ошибка при одобрении заявки от {user_id}: {e}")
        else:
            print(f"⏸️ Пользователь {user_id} не оплатил, заявка не одобрена")


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

