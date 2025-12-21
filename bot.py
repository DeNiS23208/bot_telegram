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
    get_subscription_activated_at,
    activate_subscription_days,
    save_payment,
    update_payment_status,
    get_latest_payment_id,
    get_active_pending_payment,
)
from payments import create_payment, get_payment_status, get_payment_url

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# Имя бота из переменной окружения или по умолчанию
# Правильное имя бота: work232_bot (без @)
BOT_USERNAME = os.getenv("BOT_USERNAME", "work232_bot")
# Используем простую ссылку на бота без параметров, так как параметры могут не работать в некоторых случаях
# Пользователь все равно попадет в бота и может нажать кнопку "Проверить оплату"
RETURN_URL = f"https://t.me/{BOT_USERNAME}"

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
BTN_SUPPORT = "🆘 Поддержка"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PAY_1)],
            [KeyboardButton(text=BTN_STATUS_1)],
            [KeyboardButton(text=BTN_ABOUT_1)],
            [KeyboardButton(text=BTN_CHECK_1)],
            [KeyboardButton(text=BTN_SUPPORT)],
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
    
    # Обрабатываем возврат после оплаты
    if message.text and "payment_return" in message.text:
        await message.answer(
            "Вы вернулись после оплаты.\n\n"
            "Если оплата прошла успешно, вы получите ссылку на канал в ближайшее время.\n"
            "Если оплата не прошла, нажмите кнопку ✅ Проверить оплату для проверки статуса.",
            reply_markup=main_menu(),
        )
        return
    
    # Проверяем, не вернулся ли пользователь из ЮKassa после выхода из оплаты
    # Проверяем последний платеж, созданный в последние 15 минут
    try:
        active_payment = await get_active_pending_payment(message.from_user.id, minutes=15)
        if active_payment:
            payment_id, created_at, payment_url = active_payment
            
            # Проверяем статус платежа
            status = await maybe_await(get_payment_status, payment_id)
            await update_payment_status(payment_id, status)
            
            # Если платеж pending или canceled - отправляем уведомление о выходе из оплаты
            if status in ("pending", "canceled"):
                # Проверяем, есть ли активная подписка
                expires_at = await get_subscription_expires_at(message.from_user.id)
                has_active = expires_at and expires_at > datetime.utcnow()
                
                if not has_active:
                    # Формируем сообщение с ссылкой
                    if payment_url:
                        exit_message = (
                            "❌ Оплата не произведена\n\n"
                            "Вы вышли из формы оплаты без завершения платежа.\n\n"
                            "Воспользуйтесь ссылкой, сформированной при нажатии 'Оплатить доступ на 30 дней':\n"
                            f"{payment_url}\n\n"
                            "⚠️ Ссылка действительна 10 минут с момента создания."
                        )
                    else:
                        # Если ссылка не найдена, получаем из API
                        try:
                            payment_url = await maybe_await(get_payment_url, payment_id)
                            if payment_url:
                                exit_message = (
                                    "❌ Оплата не произведена\n\n"
                                    "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                    "Воспользуйтесь ссылкой, сформированной при нажатии 'Оплатить доступ на 30 дней':\n"
                                    f"{payment_url}\n\n"
                                    "⚠️ Ссылка действительна 10 минут с момента создания."
                                )
                            else:
                                exit_message = (
                                    "❌ Оплата не произведена\n\n"
                                    "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                    "Для оплаты нажмите кнопку 💳 Оплатить доступ на 30 дней и перейдите по новой ссылке."
                                )
                        except Exception:
                            exit_message = (
                                "❌ Оплата не произведена\n\n"
                                "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                "Для оплаты нажмите кнопку 💳 Оплатить доступ на 30 дней и перейдите по новой ссылке."
                            )
                    
                    await message.answer(exit_message, reply_markup=main_menu())
                    return
    except Exception as e:
        print(f"Ошибка при проверке возврата из оплаты: {e}")
        # Продолжаем выполнение, показываем обычное приветствие
    
    await message.answer(
        "Здравствуйте вас приветствует бот Наиля Хасанова",
        reply_markup=main_menu(),
    )


@dp.message(lambda m: (m.text or "").strip() == BTN_STATUS_1)
async def sub_status(message: Message):
    expires_at = await get_subscription_expires_at(message.from_user.id)
    activated_at = await get_subscription_activated_at(message.from_user.id)

    if not expires_at:
        await message.answer("Подписка не активна ❌")
        return

    now = datetime.utcnow()
    if expires_at > now:
        # Подписка активна
        message_text = "Подписка уже активирована!\n\n"
        if activated_at:
            message_text += f"Действует с: {activated_at.date()}\n"
        message_text += f"Действует до: {expires_at.date()}\n\n"
        message_text += "Если у вас нет доступа к платному каналу, обратитесь к менеджеру."
        await message.answer(message_text)
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

    # ПЕРВЫМ ДЕЛОМ проверяем активную подписку
    expires_at = await get_subscription_expires_at(message.from_user.id)
    activated_at = await get_subscription_activated_at(message.from_user.id)
    
    if expires_at and expires_at > datetime.utcnow():
        # Подписка активна
        message_text = "Подписка уже активирована!\n\n"
        if activated_at:
            message_text += f"Действует с: {activated_at.date()}\n"
        message_text += f"Действует до: {expires_at.date()}\n\n"
        message_text += "Если у вас нет доступа к платному каналу, обратитесь к менеджеру."
        await message.answer(message_text)
        return

    # Проверяем, есть ли активный pending платеж (созданный менее 10 минут назад)
    active_payment = await get_active_pending_payment(message.from_user.id, minutes=10)
    
    if active_payment:
        payment_id, created_at, pay_url = active_payment
        # Если payment_url есть в БД, используем его, иначе получаем из API
        if not pay_url:
            pay_url = await maybe_await(get_payment_url, payment_id)
        
        if pay_url:
            await message.answer(
                "У вас уже есть активная ссылка на оплату.\n\n"
                "Чтобы оплатить, перейдите по ссылке:\n"
                f"{pay_url}\n\n"
                "После оплаты вернитесь сюда и нажмите: ✅ Проверить оплату\n\n"
                "⚠️ Ссылка действительна 10 минут с момента создания."
            )
            return
    
    # Создаем новый платеж, если активного нет
    payment_id, pay_url = await maybe_await(
        create_payment,
        amount_rub="1.00",  # Тестовая сумма 1 рубль
        description="Подписка на канал (30 дней)",
        return_url=RETURN_URL,
        customer_email=CUSTOMER_EMAIL,
        telegram_user_id=message.from_user.id,  # ✅ КРИТИЧНО
    )

    await save_payment(message.from_user.id, payment_id, status="pending")

    await message.answer(
        "Чтобы оплатить, перейдите по ссылке:\n"
        f"{pay_url}\n\n"
        "После оплаты вернитесь сюда и нажмите: ✅ Проверить оплату\n\n"
        "⚠️ Ссылка действительна 10 минут."
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
        activated_at = await get_subscription_activated_at(message.from_user.id)
        
        message_text = "✅ Оплата подтверждена!\n\n"
        if activated_at:
            message_text += f"Действует с: {activated_at.date()}\n"
        message_text += f"Действует до: {expires_at.date()}\n\n"
        message_text += "Если вы ещё не получили ссылку на канал, она должна прийти в ближайшее время."
        
        await message.answer(message_text)
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


@dp.message(lambda m: (m.text or "").strip() == BTN_SUPPORT)
async def support(message: Message):
    """Обработчик кнопки поддержки"""
    await message.answer(
        "🆘 Поддержка\n\n"
        "По всем вопросам обращайтесь к менеджеру:\n"
        "@irina_blv"
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
    # Получаем имя бота из API для обновления RETURN_URL
    try:
        bot_info = await bot.get_me()
        global BOT_USERNAME, RETURN_URL
        BOT_USERNAME = bot_info.username
        RETURN_URL = f"https://t.me/{BOT_USERNAME}"
        print(f"✅ Имя бота получено: @{BOT_USERNAME}")
    except Exception as e:
        print(f"⚠️ Не удалось получить имя бота из API: {e}, используем из .env")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

