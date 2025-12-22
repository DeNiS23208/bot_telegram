import asyncio
import os
import inspect
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ChatJoinRequest, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from dotenv import load_dotenv

from db import (
    init_db,
    ensure_user,
    get_subscription_expires_at,
    get_subscription_starts_at,
    activate_subscription_days,
    save_payment,
    update_payment_status,
    get_latest_payment_id,
    get_active_pending_payment,
    format_datetime_moscow,
    get_saved_payment_method_id,
    is_auto_renewal_enabled,
    set_auto_renewal,
    delete_payment_method,
)
from payments import create_payment, get_payment_status, get_payment_url

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# Имя бота из переменной окружения или по умолчанию
# Правильное имя бота: work232_bot (без @)
BOT_USERNAME = os.getenv("BOT_USERNAME", "work232_bot")
# URL для возврата после оплаты - используем webhook endpoint для обработки возврата
# Если не указан в env, используем домен с портом 8000
YOOKASSA_RETURN_URL = os.getenv("YOOKASSA_RETURN_URL")
if YOOKASSA_RETURN_URL:
    RETURN_URL_BASE = YOOKASSA_RETURN_URL.rstrip('/') + "/payment/return"
else:
    # Fallback на Telegram бота, если webhook URL не указан
    RETURN_URL_BASE = f"https://t.me/{BOT_USERNAME}"

# Функция для формирования return_url с user_id
def get_return_url(telegram_user_id: int) -> str:
    """Формирует return_url с telegram_user_id для обработки возврата"""
    if YOOKASSA_RETURN_URL:
        return f"{RETURN_URL_BASE}?user_id={telegram_user_id}"
    return RETURN_URL_BASE

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
BTN_UNLINK_CARD = "🔓 Отвязать карту"
BTN_DISABLE_AUTO = "Отключить автопродление"  # Временная кнопка для скриншотов
BTN_UNLINK_AND_DISABLE = "Отвязать карту и отключить автопродление"  # Временная кнопка для скриншотов


def get_auto_renewal_button_text(enabled: bool) -> str:
    """Возвращает текст кнопки автопродления с индикатором"""
    if enabled:
        return "🔄 Автопродление подписки ✅"
    else:
        return "🔄 Автопродление подписки ❌"


async def main_menu(telegram_id: int = None) -> ReplyKeyboardMarkup:
    """Создает главное меню с учетом статуса автопродления и сохраненной карты"""
    # Определяем текст кнопки автопродления
    if telegram_id:
        auto_renewal_enabled = await is_auto_renewal_enabled(telegram_id)
        auto_renewal_text = get_auto_renewal_button_text(auto_renewal_enabled)
        # Проверяем, есть ли сохраненная карта
        saved_method = await get_saved_payment_method_id(telegram_id)
        show_unlink = saved_method is not None
    else:
        auto_renewal_text = get_auto_renewal_button_text(False)
        show_unlink = False
    
    keyboard = [
        [KeyboardButton(text=BTN_PAY_1)],
        [KeyboardButton(text=BTN_STATUS_1)],
        [KeyboardButton(text=auto_renewal_text)],
    ]
    
    # Добавляем кнопку отвязки карты, если есть сохраненная карта
    if show_unlink:
        keyboard.append([KeyboardButton(text=BTN_UNLINK_CARD)])
    
    keyboard.extend([
        [KeyboardButton(text=BTN_ABOUT_1)],
        [KeyboardButton(text=BTN_CHECK_1)],
        [KeyboardButton(text=BTN_SUPPORT)],
    ])
    
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
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
            reply_markup=await main_menu(message.from_user.id),
        )
        return
    
    await message.answer(
        "Здравствуйте вас приветствует бот Наиля Хасанова",
        reply_markup=await main_menu(message.from_user.id),
    )


@dp.message(lambda m: (m.text or "").strip() == BTN_STATUS_1)
async def sub_status(message: Message):
    expires_at = await get_subscription_expires_at(message.from_user.id)

    if not expires_at:
        await message.answer("Подписка не активна ❌")
        return

    now = datetime.utcnow()
    if expires_at > now:
        starts_at = await get_subscription_starts_at(message.from_user.id)
        if starts_at:
            starts_str = format_datetime_moscow(starts_at)
            expires_str = format_datetime_moscow(expires_at)
            await message.answer(
                f"Подписка активна ✅\n\n"
                f"Подписка активна с: {starts_str}\n"
                f"Подписка активна до: {expires_str}"
            )
        else:
            # Если дата начала не найдена, используем только дату окончания
            expires_str = format_datetime_moscow(expires_at)
            await message.answer(f"Подписка активна ✅\nПодписка активна до: {expires_str}")
    else:
        expires_str = format_datetime_moscow(expires_at)
        await message.answer(f"Подписка закончилась ❌\nЗакончилась: {expires_str}")


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
    if expires_at and expires_at > datetime.utcnow():
        starts_at = await get_subscription_starts_at(message.from_user.id)
        starts_str = format_datetime_moscow(starts_at) if starts_at else "неизвестно"
        expires_str = format_datetime_moscow(expires_at)
        await message.answer(
            f"✅ Подписка уже активирована!\n\n"
            f"Подписка активна с: {starts_str}\n"
            f"Подписка активна до: {expires_str}\n\n"
            f"Если у вас нет доступа к платному каналу, обратитесь к менеджеру."
        )
        return

    # Проверяем, есть ли активный pending платеж (созданный менее 10 минут назад)
    active_payment = await get_active_pending_payment(message.from_user.id, minutes=10)
    
    if active_payment:
        payment_id, created_at = active_payment
        # Получаем ссылку на оплату для существующего платежа
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
    return_url_with_user = get_return_url(message.from_user.id)
    # Пытаемся создать платеж с возможностью сохранения способа оплаты для автопродления
    # Если магазин не настроен для автоплатежей, платеж будет создан без этого параметра
    payment_id, pay_url = await maybe_await(
        create_payment,
        amount_rub="1.00",  # Тестовая сумма 1 рубль
        description="Подписка на канал (30 дней)",
        return_url=return_url_with_user,
        customer_email=CUSTOMER_EMAIL,
        telegram_user_id=message.from_user.id,  # ✅ КРИТИЧНО
        enable_save_payment_method=True,  # Пытаемся включить сохранение способа оплаты
    )

    await save_payment(message.from_user.id, payment_id, status="pending")

    # Создаем кнопку оплаты с URL
    pay_button = InlineKeyboardButton(text="Оплатить ❤️", url=pay_url)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[pay_button]])

    # Формируем сообщение с информацией о подписке
    subscription_text = (
        "Стоимость подписки\n\n"
        "1 месяц — 1 рубль\n\n"
        "Продление подписки происходит автоматически — каждые 30 дней.\n\n"
        'Нажимая "Оплатить", вы даете согласие на регулярные списания, на обработку '
        '<a href="https://example.com/privacy">персональных данных</a> и принимаете условия '
        '<a href="https://example.com/offer">публичной оферты</a>.\n\n'
        "Получить доступ в закрытый канал"
    )

    await message.answer(
        subscription_text,
        reply_markup=keyboard,
        parse_mode="HTML"
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
        starts_at, expires_at = await activate_subscription_days(message.from_user.id, days=30)
        starts_str = format_datetime_moscow(starts_at)
        expires_str = format_datetime_moscow(expires_at)
        await message.answer(
            f"✅ Оплата подтверждена!\n\n"
            f"Подписка активна с: {starts_str}\n"
            f"Подписка активна до: {expires_str}\n\n"
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


@dp.message(lambda m: (m.text or "").strip() == BTN_SUPPORT)
async def support(message: Message):
    """Обработчик кнопки поддержки"""
    await message.answer(
        "🆘 Поддержка\n\n"
        "По всем вопросам обращайтесь к менеджеру:\n"
        "@otd_zabota"
    )


@dp.message(lambda m: (m.text or "").strip().startswith("🔄 Автопродление"))
async def auto_renewal_toggle(message: Message):
    """Обработчик кнопки автопродления подписки - показывает inline-кнопки для управления"""
    user_id = message.from_user.id
    
    # Проверяем текущий статус автопродления
    current_status = await is_auto_renewal_enabled(user_id)
    saved_method = await get_saved_payment_method_id(user_id)
    
    # Создаем inline-клавиатуру с кнопками управления (всегда показываем для скриншотов)
    keyboard_buttons = []
    
    # Кнопка "Отключить автопродление" - всегда показываем
    keyboard_buttons.append([InlineKeyboardButton(
        text=BTN_DISABLE_AUTO,
        callback_data="disable_auto_renewal"
    )])
    
    # Кнопка "Отвязать карту и отключить автопродление" - всегда показываем
    keyboard_buttons.append([InlineKeyboardButton(
        text=BTN_UNLINK_AND_DISABLE,
        callback_data="unlink_and_disable"
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
    
    # Формируем сообщение о текущем статусе
    if current_status:
        status_text = "✅ Автопродление подписки включено"
        expires_at = await get_subscription_expires_at(user_id)
        if expires_at:
            next_payment_str = format_datetime_moscow(expires_at)
            status_text += f"\n\nСледующее автоматическое списание: {next_payment_str}"
    else:
        status_text = "❌ Автопродление подписки выключено"
        if not saved_method:
            status_text += "\n\n⚠️ Для включения автопродления необходимо сохранить данные карты при оплате"
    
    await message.answer(
        f"🔄 Управление автопродлением\n\n{status_text}\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )


@dp.message(lambda m: (m.text or "").strip() == BTN_DISABLE_AUTO)
async def disable_auto_renewal(message: Message):
    """Обработчик кнопки отключения автопродления (временная для скриншотов)"""
    user_id = message.from_user.id
    
    # Проверяем текущий статус автопродления
    current_status = await is_auto_renewal_enabled(user_id)
    
    if not current_status:
        await message.answer(
            "ℹ️ Автопродление уже отключено.",
            reply_markup=await main_menu(user_id)
        )
        return
    
    # Отключаем автопродление
    await set_auto_renewal(user_id, False)
    await message.answer(
        "❌ Автопродление подписки отключено\n\n"
        "Ваша подписка не будет автоматически продлеваться.",
        reply_markup=await main_menu(user_id)
    )


@dp.message(lambda m: (m.text or "").strip() == BTN_UNLINK_CARD)
async def unlink_card(message: Message):
    """Обработчик кнопки отвязки карты"""
    user_id = message.from_user.id
    
    # Проверяем, есть ли сохраненная карта
    saved_method = await get_saved_payment_method_id(user_id)
    
    if not saved_method:
        await message.answer(
            "ℹ️ У вас нет привязанной карты.\n\n"
            "Карта будет сохранена при оплате, если вы отметите галочку «Запомнить данные карты» на форме оплаты.",
            reply_markup=await main_menu(user_id)
        )
        return
    
    # Удаляем способ оплаты и отключаем автопродление
    deleted = await delete_payment_method(user_id)
    
    if deleted:
        await message.answer(
            "✅ Карта успешно отвязана\n\n"
            "Автопродление подписки отключено.\n"
            "Данные карты удалены из системы.\n\n"
            "Для оплаты в следующий раз вам нужно будет ввести данные карты заново.",
            reply_markup=await main_menu(user_id)
        )
    else:
        await message.answer(
            "❌ Ошибка при отвязке карты\n\n"
            "Пожалуйста, попробуйте позже или обратитесь в поддержку.",
            reply_markup=await main_menu(user_id)
        )


@dp.callback_query(lambda c: c.data == "disable_auto_renewal")
async def disable_auto_renewal_callback(callback: CallbackQuery):
    """Обработчик callback для отключения автопродления"""
    user_id = callback.from_user.id
    
    # Отключаем автопродление
    await set_auto_renewal(user_id, False)
    
    await callback.answer("Автопродление отключено")
    await callback.message.edit_text(
        "❌ Автопродление подписки отключено\n\n"
        "Ваша подписка не будет автоматически продлеваться.",
        reply_markup=None
    )


@dp.callback_query(lambda c: c.data == "unlink_and_disable")
async def unlink_and_disable_callback(callback: CallbackQuery):
    """Обработчик callback для отвязки карты и отключения автопродления"""
    user_id = callback.from_user.id
    
    # Проверяем, есть ли сохраненная карта
    saved_method = await get_saved_payment_method_id(user_id)
    
    if saved_method:
        # Удаляем способ оплаты и отключаем автопродление
        await delete_payment_method(user_id)
        await callback.answer("Карта отвязана и автопродление отключено")
        await callback.message.edit_text(
            "✅ Карта успешно отвязана\n"
            "❌ Автопродление подписки отключено\n\n"
            "Данные карты удалены из системы.\n"
            "Ваша подписка не будет автоматически продлеваться.",
            reply_markup=None
        )
    else:
        # Если карты нет, просто отключаем автопродление
        await set_auto_renewal(user_id, False)
        await callback.answer("Автопродление отключено")
        await callback.message.edit_text(
            "ℹ️ У вас нет привязанной карты.\n"
            "❌ Автопродление подписки отключено\n\n"
            "Ваша подписка не будет автоматически продлеваться.",
            reply_markup=None
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

