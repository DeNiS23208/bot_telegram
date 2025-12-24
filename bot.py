import asyncio
import os
import inspect
import aiohttp
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ChatJoinRequest, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile, BufferedInputFile, ContentType, WebAppInfo
from aiogram.enums import ChatAction
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
    get_saved_payment_method_id,
    is_auto_renewal_enabled,
    set_auto_renewal,
    delete_payment_method,
    is_user_allowed,
    get_invite_link,
)
from utils import format_datetime_moscow
from payments import create_payment, get_payment_status, get_payment_url
from config import (
    PAYMENT_LINK_VALID_MINUTES,
    SUBSCRIPTION_DAYS,
    PAYMENT_AMOUNT_RUB,
    MAX_VIDEO_SIZE_MB,
    MAX_ANIMATION_SIZE_MB,
    MAX_ANIMATION_DURATION_SECONDS,
)

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# Имя бота из переменной окружения или по умолчанию
# Правильное имя бота: work232_bot (без @)
BOT_USERNAME = os.getenv("BOT_USERNAME", "work232_bot")

# Функция для формирования return_url - всегда ведет на бота
def get_return_url(telegram_user_id: int) -> str:
    """Формирует return_url - всегда ведет на бота"""
    # Всегда возвращаем ссылку на бота, чтобы пользователь вернулся в бота после оплаты
    return f"https://t.me/{BOT_USERNAME}"

# Для MVP можно фиксированный email, потом заменим на ввод пользователем
CUSTOMER_EMAIL = os.getenv("PAYMENT_CUSTOMER_EMAIL", "test@example.com")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")

bot = Bot(token=TOKEN)
dp = Dispatcher()


BTN_PAY_1 = "💳 Оплатить подписку"  # Показывается если нет подписки
BTN_MANAGE_SUB = "⚙️ Управление подпиской"  # Показывается если есть подписка
BTN_CANCEL_SUB = "❌ Отменить подписку"  # Показывается в меню управления если автопродление включено
BTN_RESUME_SUB = "▶️ Возобновить подписку"  # Показывается в меню управления если автопродление отключено
BTN_STATUS_1 = "📊 Статус подписки"
BTN_ABOUT_1 = "ℹ️ О проекте"
BTN_CHECK_1 = "🔍 Проверить оплату"
BTN_SUPPORT = "💬 Поддержка"
BTN_CHANNEL_LINK = "🔗 Ссылка на канал"  # Показывается только если есть активная подписка


async def main_menu(telegram_id: int = None) -> ReplyKeyboardMarkup:
    """Создает главное меню с учетом статуса подписки"""
    # Определяем, какая кнопка показывается: "Оплатить подписку" или "Управление подпиской"
    if telegram_id:
        expires_at = await get_subscription_expires_at(telegram_id)
        now = datetime.utcnow()
        has_active_subscription = expires_at and expires_at > now
    else:
        has_active_subscription = False
    
    # Если есть активная подписка - показываем "Управление подпиской", иначе "Оплатить подписку"
    payment_button = BTN_MANAGE_SUB if has_active_subscription else BTN_PAY_1
    
    keyboard = [
        [KeyboardButton(text=payment_button)],
        [KeyboardButton(text=BTN_STATUS_1)],
    ]
    
    # Добавляем кнопку "Ссылка на канал" только если есть активная подписка
    if has_active_subscription:
        keyboard.append([KeyboardButton(text=BTN_CHANNEL_LINK)])
    
    keyboard.extend([
        [KeyboardButton(text=BTN_ABOUT_1)],
        [KeyboardButton(text=BTN_CHECK_1)],
        [KeyboardButton(text=BTN_SUPPORT)],
    ])
    
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
    )


async def manage_subscription_menu(telegram_id: int) -> ReplyKeyboardMarkup:
    """Создает меню управления подпиской с кнопкой отмены/возобновления в зависимости от статуса автопродления"""
    # Проверяем статус автопродления
    auto_renewal_enabled = await is_auto_renewal_enabled(telegram_id)
    
    # Если автопродление включено - показываем "Отменить подписку", иначе "Возобновить подписку"
    action_button = BTN_CANCEL_SUB if auto_renewal_enabled else BTN_RESUME_SUB
    
    keyboard = [
        [KeyboardButton(text=action_button)],
        [KeyboardButton(text="◀️ Назад в меню")],
    ]
    
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
    )


async def maybe_await(func, *args, **kwargs):
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def send_typing_action(chat_id: int):
    """Показывает индикатор 'печатает...' для визуальной обратной связи"""
    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(0.5)  # Небольшая задержка для красивого эффекта
    except:
        pass


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username)
    
    # Обрабатываем возврат после оплаты
    if message.text and "payment_return" in message.text:
        await message.answer(
            "👋 <b>Вы вернулись после оплаты</b>\n\n"
            "✅ Если оплата прошла успешно, ссылка на канал придет в ближайшее время.\n\n"
            "🔍 Если ссылки нет, нажмите кнопку 🔍 Проверить оплату для проверки статуса.",
            parse_mode="HTML",
            reply_markup=await main_menu(message.from_user.id),
        )
        return
    
    # Путь к видео или URL
    # Приоритет: 1) локальный файл ScreenRecording, 2) WELCOME_VIDEO_PATH, 3) WELCOME_VIDEO_URL
    VIDEO_RECORDING_PATH = os.path.join(os.path.dirname(__file__), "ScreenRecording_12-23-2025 10-50-29_1.MP4")
    VIDEO_PATH = os.getenv("WELCOME_VIDEO_PATH", "/opt/bot_telegram/welcome_video.mp4")
    VIDEO_GIF_PATH = os.getenv("WELCOME_VIDEO_GIF_PATH", "/opt/bot_telegram/welcome_video.gif")  # GIF для авто-воспроизведения
    VIDEO_URL = os.getenv("WELCOME_VIDEO_URL", None)  # Можно указать URL видео
    
    # Приоритет: сначала пробуем локальный файл (быстрее), потом URL
    
    # Текст приветственного сообщения
    welcome_text = (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Меня зовут Наиль Хасанов, и я рад приветствовать вас в нашем боте.\n\n"
        "🎯 Здесь вы можете:\n"
        "• Получить доступ к закрытому каналу\n"
        "• Управлять своей подпиской\n"
        "• Настроить автопродление\n\n"
        "Выберите действие в меню ниже 👇"
    )
    
    # Отправляем видео с текстом в caption (встроено в сообщение)
    video_sent = False

    # ПРИОРИТЕТ 1: Сначала пробуем файл ScreenRecording
    if os.path.exists(VIDEO_RECORDING_PATH):
        try:
            file_size = os.path.getsize(VIDEO_RECORDING_PATH)
            file_size_mb = file_size / 1024 / 1024
            print(f"📹 Найден файл ScreenRecording, размер: {file_size_mb:.1f}MB")
            
            video_file = FSInputFile(VIDEO_RECORDING_PATH)
            max_video_size = MAX_VIDEO_SIZE_MB * 1024 * 1024
            
            if file_size > max_video_size:
                print(f"⚠️ Видео слишком большое ({file_size_mb:.1f}MB), отправляю как документ")
                await bot.send_document(
                    chat_id=message.chat.id,
                    document=video_file,
                    caption=welcome_text,
                    parse_mode="HTML",
                    reply_markup=await main_menu(message.from_user.id),
                )
                print(f"✅ Видео отправлено как документ: {VIDEO_RECORDING_PATH}")
            else:
                await bot.send_video(
                    chat_id=message.chat.id,
                    video=video_file,
                    caption=welcome_text,
                    parse_mode="HTML",
                    supports_streaming=True,
                    reply_markup=await main_menu(message.from_user.id),
                )
                print(f"✅ Видео успешно отправлено: {VIDEO_RECORDING_PATH}")
            video_sent = True
            return  # Прерываем выполнение
        except Exception as e:
            print(f"⚠️ Ошибка отправки ScreenRecording: {e}")
            import traceback
            traceback.print_exc()

    # ПРИОРИТЕТ 2: Пробуем GIF файл для автоматического воспроизведения в Desktop
    # GIF анимации в Telegram могут автоматически воспроизводиться при прокрутке
    if not video_sent and os.path.exists(VIDEO_GIF_PATH):
        try:
            gif_size = os.path.getsize(VIDEO_GIF_PATH)
            gif_size_mb = gif_size / 1024 / 1024
            print(f"🎬 Найден GIF файл для авто-воспроизведения, размер: {gif_size_mb:.1f}MB")
            
            # Telegram ограничение для animation: ~50MB
            max_gif_size = 50 * 1024 * 1024  # 50MB
            if gif_size <= max_gif_size:
                gif_file = FSInputFile(VIDEO_GIF_PATH)
                await bot.send_animation(
                    chat_id=message.chat.id,
                    animation=gif_file,
                    caption=welcome_text,
                    parse_mode="HTML",
                    reply_markup=await main_menu(message.from_user.id),
                )
                print(f"✅ GIF анимация отправлена для авто-воспроизведения: {VIDEO_GIF_PATH}")
                video_sent = True
                return  # Прерываем выполнение, GIF отправлен
            else:
                print(f"⚠️ GIF слишком большой ({gif_size_mb:.1f}MB), пробуем обычное видео")
        except Exception as gif_error:
            print(f"⚠️ Ошибка отправки GIF: {gif_error}, пробуем обычное видео")
            import traceback
            traceback.print_exc()

    # Если GIF нет или не сработал, пробуем обычное видео
    if not video_sent and os.path.exists(VIDEO_PATH):
        try:
            file_size = os.path.getsize(VIDEO_PATH)
            file_size_mb = file_size / 1024 / 1024
            print(f"📹 Найден локальный файл видео, размер: {file_size_mb:.1f}MB")
            
            # Создаем FSInputFile из локального файла
            video_file = FSInputFile(VIDEO_PATH)
            
            # Проверяем размер - Telegram ограничение для send_video
            max_video_size = MAX_VIDEO_SIZE_MB * 1024 * 1024
            if file_size > max_video_size:
                # Если видео слишком большое, используем send_document
                print(f"⚠️ Видео слишком большое ({file_size_mb:.1f}MB), отправляю как документ")
                await bot.send_document(
                    chat_id=message.chat.id,
                    document=video_file,
                    caption=welcome_text,
                    parse_mode="HTML",
                    reply_markup=await main_menu(message.from_user.id),
                )
                print(f"✅ Видео отправлено как документ из файла: {VIDEO_PATH}")
            else:
                # Отправляем как видео с оптимизацией для быстрой загрузки
                # Получаем информацию о видео для лучшей оптимизации
                try:
                    import subprocess
                    duration_cmd = f'ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=noprint_wrappers=1:nokey=1 "{VIDEO_PATH}"'
                    duration_result = subprocess.run(duration_cmd, shell=True, capture_output=True, text=True, timeout=5)
                    duration = int(float(duration_result.stdout.strip())) if duration_result.returncode == 0 else None
                    
                    width_cmd = f'ffprobe -v error -select_streams v:0 -show_entries stream=width -of default=noprint_wrappers=1:nokey=1 "{VIDEO_PATH}"'
                    width_result = subprocess.run(width_cmd, shell=True, capture_output=True, text=True, timeout=5)
                    width = int(width_result.stdout.strip()) if width_result.returncode == 0 else None
                    
                    height_cmd = f'ffprobe -v error -select_streams v:0 -show_entries stream=height -of default=noprint_wrappers=1:nokey=1 "{VIDEO_PATH}"'
                    height_result = subprocess.run(height_cmd, shell=True, capture_output=True, text=True, timeout=5)
                    height = int(height_result.stdout.strip()) if height_result.returncode == 0 else None
                    
                    # Пробуем отправить как animation (GIF) для авто-воспроизведения в Desktop
                    # Но только если видео короткое и небольшое
                    # В Telegram animation может автоматически воспроизводиться в Desktop при прокрутке
                    should_try_animation = (
                        duration and duration <= MAX_ANIMATION_DURATION_SECONDS and 
                        file_size_mb <= MAX_ANIMATION_SIZE_MB
                    )
                    
                    if should_try_animation:
                        try:
                            print(f"🎬 Пробую отправить как animation для авто-воспроизведения...")
                            await bot.send_animation(
                                chat_id=message.chat.id,
                                animation=video_file,
                                caption=welcome_text,
                                parse_mode="HTML",
                                reply_markup=await main_menu(message.from_user.id),
                            )
                            print(f"✅ Видео отправлено как animation для авто-воспроизведения: {VIDEO_PATH}")
                            return  # Успешно отправлено как animation
                        except Exception as anim_error:
                            print(f"⚠️ Не удалось отправить как animation: {anim_error}, отправляю как обычное видео")
                    
                    # Отправляем как обычное видео с оптимизацией
                    video_params = {
                        "chat_id": message.chat.id,
                        "video": video_file,
                        "caption": welcome_text,
                        "parse_mode": "HTML",
                        "supports_streaming": True,  # Включаем потоковое воспроизведение
                        "reply_markup": await main_menu(message.from_user.id),
                    }
                    
                    # Добавляем метаданные видео для лучшей оптимизации
                    if duration:
                        video_params["duration"] = duration
                    if width and height:
                        video_params["width"] = width
                        video_params["height"] = height
                    
                    await bot.send_video(**video_params)
                    print(f"✅ Видео успешно отправлено из файла: {VIDEO_PATH}")
                except Exception as meta_error:
                    # Если не удалось получить метаданные, отправляем без них
                    print(f"⚠️ Не удалось получить метаданные видео: {meta_error}, отправляю без них")
                    await bot.send_video(
                        chat_id=message.chat.id,
                        video=video_file,
                        caption=welcome_text,
                        parse_mode="HTML",
                        supports_streaming=True,
                        reply_markup=await main_menu(message.from_user.id),
                    )
                    print(f"✅ Видео успешно отправлено из файла: {VIDEO_PATH}")
            return  # Прерываем выполнение, чтобы не отправлять дублирующее сообщение
        except Exception as e:
            print(f"⚠️ Ошибка отправки видео из файла: {e}")
            import traceback
            traceback.print_exc()
    
    # Если локальный файл не найден или не сработал, пробуем URL
    # Telegram не принимает прямой URL для видео, нужно скачать и отправить как файл
    if not video_sent and VIDEO_URL:
        try:
            print(f"📥 Скачиваю видео с URL: {VIDEO_URL}")
            async with aiohttp.ClientSession() as session:
                async with session.get(VIDEO_URL, ssl=False) as response:
                    if response.status == 200:
                        video_data = await response.read()
                        video_size_mb = len(video_data) / 1024 / 1024
                        print(f"✅ Видео скачано, размер: {video_size_mb:.1f}MB")
                        
                        # Создаем BufferedInputFile из скачанных данных
                        video_file = BufferedInputFile(
                            file=video_data,
                            filename="welcome_video.mp4"
                        )
                        
                        # Проверяем размер - Telegram ограничение для send_video
                        max_video_size = MAX_VIDEO_SIZE_MB * 1024 * 1024
                        if len(video_data) > max_video_size:
                            # Если видео слишком большое, используем send_document
                            print(f"⚠️ Видео слишком большое ({video_size_mb:.1f}MB), отправляю как документ")
                            await bot.send_document(
                                chat_id=message.chat.id,
                                document=video_file,
                                caption=welcome_text,
                                parse_mode="HTML",
                                reply_markup=await main_menu(message.from_user.id),
                            )
                            print(f"✅ Видео отправлено как документ по URL: {VIDEO_URL}")
                        else:
                            # Пробуем отправить как animation для авто-воспроизведения в Desktop
                            # Animation может автоматически воспроизводиться в Desktop при прокрутке
                            try:
                                # Пытаемся получить длительность из метаданных (если доступно)
                                # Для простоты проверяем только размер
                                if video_size_mb <= MAX_ANIMATION_SIZE_MB:
                                    print(f"🎬 Пробую отправить как animation для авто-воспроизведения...")
                                    await bot.send_animation(
                                        chat_id=message.chat.id,
                                        animation=video_file,
                                        caption=welcome_text,
                                        parse_mode="HTML",
                                        reply_markup=await main_menu(message.from_user.id),
                                    )
                                    print(f"✅ Видео отправлено как animation по URL: {VIDEO_URL}")
                                    video_sent = True
                                    return  # Прерываем выполнение
                            except Exception as anim_error:
                                print(f"⚠️ Не удалось отправить как animation: {anim_error}, отправляю как обычное видео")
                            
                            # Отправляем как обычное видео с оптимизацией
                            await bot.send_video(
                                chat_id=message.chat.id,
                                video=video_file,
                                caption=welcome_text,
                                parse_mode="HTML",
                                supports_streaming=True,  # Включаем потоковое воспроизведение для быстрой загрузки
                                reply_markup=await main_menu(message.from_user.id),
                            )
                            print(f"✅ Видео успешно отправлено по URL: {VIDEO_URL}")
                        return  # Прерываем выполнение, чтобы не отправлять дублирующее сообщение
                    else:
                        print(f"⚠️ Ошибка загрузки видео: HTTP {response.status}")
        except Exception as e:
            print(f"⚠️ Ошибка отправки видео по URL: {e}")
            import traceback
            traceback.print_exc()
    
    
    # Если видео не удалось отправить, отправляем только текст
    if not video_sent:
        print("⚠️ Видео не отправлено, отправляем только текст")
        await message.answer(
            welcome_text,
            parse_mode="HTML",
            reply_markup=await main_menu(message.from_user.id),
        )
        return  # Важно: прерываем выполнение, чтобы не было дублирования


@dp.message(lambda m: (m.text or "").strip() == BTN_STATUS_1)
async def sub_status(message: Message):
    await send_typing_action(message.chat.id)
    
    expires_at = await get_subscription_expires_at(message.from_user.id)
    
    if not expires_at:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "❌ <b>Подписка не активна</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 Для получения доступа нажмите кнопку 💳 Оплатить подписку",
            parse_mode="HTML"
        )
        return

    now = datetime.utcnow()
    if expires_at > now:
        starts_at = await get_subscription_starts_at(message.from_user.id)
        if starts_at:
            starts_str = format_datetime_moscow(starts_at)
            expires_str = format_datetime_moscow(expires_at)
            await message.answer(
                "━━━━━━━━━━━━━━━━━━━━\n"
                "✅ <b>Подписка активна</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 <b>Активна с:</b> {starts_str}\n"
                f"📅 <b>Активна до:</b> {expires_str}\n\n"
                "🎉 <b>У вас есть полный доступ к закрытому каналу!</b>",
                parse_mode="HTML"
            )
        else:
            # Если дата начала не найдена, используем только дату окончания
            expires_str = format_datetime_moscow(expires_at)
            await message.answer(
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ <b>Подписка активна</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 <b>Активна до:</b> {expires_str}",
                parse_mode="HTML"
            )
    else:
        expires_str = format_datetime_moscow(expires_at)
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ <b>Подписка закончилась</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 <b>Закончилась:</b> {expires_str}\n\n"
            "💡 Для продления подписки нажмите кнопку 💳 Оплатить подписку",
            parse_mode="HTML"
        )


@dp.message(lambda m: (m.text or "").strip() == BTN_ABOUT_1)
async def about(message: Message):
    await message.answer(
        "📖 <b>О проекте</b>\n\n"
        "Это бот для доступа к закрытому Telegram-каналу.\n\n"
        "🔐 <b>Безопасность:</b>\n"
        "• Оплата через ЮKassa\n"
        "• Защищенные платежи\n"
        "• Автоматическое управление доступом\n\n"
        "✨ <b>Удобство:</b>\n"
        "• Простая оплата\n"
        "• Автопродление подписки\n"
        "• Мгновенный доступ к контенту",
        parse_mode="HTML"
    )


# Обработчик для кнопки "Оплатить подписку" (когда нет подписки)
@dp.message(lambda m: (m.text or "").strip() == BTN_PAY_1)
async def pay(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username)
    await send_typing_action(message.chat.id)

    # ПЕРВЫМ ДЕЛОМ проверяем активную подписку
    expires_at = await get_subscription_expires_at(message.from_user.id)
    
    if expires_at and expires_at > datetime.utcnow():
        starts_at = await get_subscription_starts_at(message.from_user.id)
        starts_str = format_datetime_moscow(starts_at) if starts_at else "неизвестно"
        expires_str = format_datetime_moscow(expires_at)
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Подписка уже активирована!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 <b>Активна с:</b> {starts_str}\n"
            f"📅 <b>Активна до:</b> {expires_str}\n\n"
            f"💬 Если у вас нет доступа к платному каналу, обратитесь к менеджеру: @otd_zabota\n\n"
            f"⚙️ Для управления подпиской нажмите кнопку «{BTN_MANAGE_SUB}»",
            parse_mode="HTML"
        )
        return

    # Проверяем, есть ли активный pending платеж (созданный менее N минут назад)
    active_payment = await get_active_pending_payment(message.from_user.id, minutes=PAYMENT_LINK_VALID_MINUTES)
    
    if active_payment:
        payment_id, created_at = active_payment
        # Получаем ссылку на оплату для существующего платежа
        pay_url = await maybe_await(get_payment_url, payment_id)
        
        if pay_url:
            pay_button = InlineKeyboardButton(text="💳 Перейти к оплате", url=pay_url)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[pay_button]])
            await message.answer(
                f"⏳ <b>У вас уже есть активная ссылка на оплату</b>\n\n"
                "Нажмите на кнопку ниже, чтобы перейти к оплате:\n\n"
                f"⚠️ <i>Ссылка действительна {PAYMENT_LINK_VALID_MINUTES} минут с момента создания</i>\n\n"
                "После оплаты нажмите: 🔍 Проверить оплату",
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return
    
    # Создаем новый платеж, если активного нет
    return_url_with_user = get_return_url(message.from_user.id)
    # Пытаемся создать платеж с возможностью сохранения способа оплаты для автопродления
    # Если магазин не настроен для автоплатежей, платеж будет создан без этого параметра
    payment_id, pay_url = await maybe_await(
        create_payment,
        amount_rub=PAYMENT_AMOUNT_RUB,
        description=f"Подписка на канал ({SUBSCRIPTION_DAYS} дней)",
        return_url=return_url_with_user,
        customer_email=CUSTOMER_EMAIL,
        telegram_user_id=message.from_user.id,  # ✅ КРИТИЧНО
        enable_save_payment_method=True,  # Пытаемся включить сохранение способа оплаты
    )

    await save_payment(message.from_user.id, payment_id, status="pending")

    # Создаем кнопку оплаты с URL
    pay_button = InlineKeyboardButton(text="💳 Оплатить 1₽", url=pay_url)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[pay_button]])

    # Формируем сообщение с информацией о подписке
    # Используем HTML-теги для ссылок, чтобы избежать превью документа от Яндекс Диска
    subscription_text = (
        "💰 <b>Оформление подписки</b>\n\n"
        f"💎 <b>Стоимость:</b> 1 месяц — {PAYMENT_AMOUNT_RUB} рубль\n\n"
        f"🔄 <b>Автопродление:</b> каждые {SUBSCRIPTION_DAYS} дней\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📋 Нажимая кнопку оплаты, вы соглашаетесь с:\n"
        "• Обработкой <a href=\"https://disk.yandex.ru/i/QadGJAMYKqbKpQ\">персональных данных</a>\n"
        "• Условиями <a href=\"https://disk.yandex.ru/i/fXUDJfj_i5cYIA\">публичной оферты</a>\n"
        "• Регулярными списаниями при включенном автопродлении\n\n"
        "🎁 После оплаты вы получите доступ к закрытому каналу"
    )

    await message.answer(
        subscription_text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True  # Отключаем превью ссылок, чтобы документ не отображался
    )


@dp.message(lambda m: (m.text or "").strip() == BTN_CHECK_1)
async def check_payment(message: Message):
    await send_typing_action(message.chat.id)
    
    payment_id = await get_latest_payment_id(message.from_user.id)

    if not payment_id:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔍 <b>Платежи не найдены</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
                "💡 Сначала нажмите 💳 Оплатить подписку, чтобы создать платеж.",
            parse_mode="HTML"
        )
        return

    status = await maybe_await(get_payment_status, payment_id)
    await update_payment_status(payment_id, status)

    if status == "succeeded":
        starts_at, expires_at = await activate_subscription_days(message.from_user.id, days=SUBSCRIPTION_DAYS)
        starts_str = format_datetime_moscow(starts_at)
        expires_str = format_datetime_moscow(expires_at)
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✅ <b>Оплата подтверждена!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 <b>Подписка активна с:</b> {starts_str}\n"
            f"📅 <b>Подписка активна до:</b> {expires_str}\n\n"
            "🎉 <b>Ссылка на канал должна прийти в ближайшее время!</b>\n"
            "💬 Если ссылка не пришла, обратитесь в поддержку: @otd_zabota",
            parse_mode="HTML"
        )
    elif status in ("pending", "waiting_for_capture"):
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⏳ <b>Платёж обрабатывается</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Статус: <i>ожидание оплаты</i>\n\n"
            "💡 <b>Что делать:</b>\n"
            "• Если вы уже оплатили, подождите 2-3 минуты\n"
            "• Нажмите эту кнопку ещё раз для проверки\n"
            "• Если оплата не проходит, попробуйте оплатить заново",
            parse_mode="HTML"
        )
    elif status == "canceled":
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "❌ <b>Платёж отменён</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Оплата не была завершена.\n\n"
            "💡 <b>Возможные причины:</b>\n"
            "• Недостаточно средств на карте\n"
            "• Операция была отменена\n"
            "• Истекло время ожидания оплаты\n\n"
            "🔄 Попробуйте оплатить снова, нажав кнопку 💳 Оплатить подписку",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ <b>Статус платежа:</b> {status}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 Если оплата не прошла, попробуйте оплатить заново.",
            parse_mode="HTML"
        )


@dp.message(lambda m: (m.text or "").strip() == BTN_SUPPORT)
async def support(message: Message):
    """Обработчик кнопки поддержки"""
    await send_typing_action(message.chat.id)
    
    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💬 <b>Поддержка</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "По всем вопросам обращайтесь к нашему менеджеру:\n\n"
        "👤 <b>@otd_zabota</b>\n\n"
        "Мы поможем с:\n"
        "• Вопросом по оплате\n"
        "• Доступом к каналу\n"
        "• Техническими проблемами\n"
        "• Любыми другими вопросами",
        parse_mode="HTML"
    )


@dp.message(lambda m: (m.text or "").strip() == BTN_CHANNEL_LINK)
async def send_channel_link(message: Message):
    """Обработчик кнопки отправки ссылки на канал"""
    await send_typing_action(message.chat.id)
    user_id = message.from_user.id
    
    # Проверяем, есть ли активная подписка
    expires_at = await get_subscription_expires_at(user_id)
    now = datetime.utcnow()
    
    if not expires_at or expires_at <= now:
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "❌ <b>Подписка не активна</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "У вас нет активной подписки для доступа к каналу.\n\n"
            "💡 Для получения доступа нажмите кнопку 💳 Оплатить подписку",
            parse_mode="HTML"
        )
        return
    
    # Получаем ссылку на канал из БД
    invite_link = await get_invite_link(user_id)
    
    if invite_link:
        expires_str = format_datetime_moscow(expires_at)
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔗 <b>Ссылка на канал</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 <b>Подписка активна до:</b> {expires_str}\n\n"
            "Нажмите на ссылку ниже, чтобы попасть в канал:\n\n"
            f"{invite_link}\n\n"
            "💡 Если ссылка не работает, обратитесь в поддержку: @otd_zabota",
            parse_mode="HTML"
        )
    else:
        # Если ссылка не найдена в БД, пытаемся создать новую
        try:
            from aiogram import Bot
            bot_instance = Bot(token=TOKEN)
            
            # Пытаемся создать новую ссылку
            invite = await bot_instance.create_chat_invite_link(
                chat_id=CHANNEL_ID,
                member_limit=1,
                expire_date=expires_at
            )
            invite_link = invite.invite_link
            
            expires_str = format_datetime_moscow(expires_at)
            await message.answer(
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🔗 <b>Ссылка на канал</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 <b>Подписка активна до:</b> {expires_str}\n\n"
                "Нажмите на ссылку ниже, чтобы попасть в канал:\n\n"
                f"{invite_link}\n\n"
                "💡 Если ссылка не работает, обратитесь в поддержку: @otd_zabota",
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"❌ Ошибка создания ссылки на канал: {e}")
            await message.answer(
                "━━━━━━━━━━━━━━━━━━━━\n"
                "❌ <b>Ошибка получения ссылки</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Не удалось получить ссылку на канал.\n\n"
                "💬 Обратитесь в поддержку: @otd_zabota\n\n"
                "Мы поможем вам получить доступ к каналу.",
                parse_mode="HTML"
            )


@dp.message(Command("send_miniapp_to_channel"))
async def cmd_send_miniapp_to_channel(message: Message):
    """Команда для отправки кнопки НАВИГАЦИЯ (mini app) в канал"""
    if not CHANNEL_ID:
        await message.answer(
            "❌ <b>Ошибка</b>\n\n"
            "CHANNEL_ID не настроен в .env файле.",
            parse_mode="HTML"
        )
        return
    
    mini_app_url = os.getenv("MINI_APP_URL", None)
    
    if not mini_app_url:
        await message.answer(
            "❌ <b>Ошибка</b>\n\n"
            "MINI_APP_URL не настроен в .env файле.\n\n"
            "Добавьте MINI_APP_URL=https://t.me/xasanimbot/miniapp в .env файл.",
            parse_mode="HTML"
        )
        return
    
    try:
        # Создаем кнопку с WebApp для открытия mini app
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="НАВИГАЦИЯ",
                    web_app=WebAppInfo(url=mini_app_url)
                )
            ]]
        )
        
        # Отправляем сообщение с кнопкой в канал
        # Telegram требует, чтобы сообщение имело текст, даже если есть кнопка
        sent_message = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=" ",  # Минимальный текст (пробел) - требуется Telegram API
            reply_markup=keyboard
        )
        
        await message.answer(
            "✅ <b>Успешно!</b>\n\n"
            f"Кнопка НАВИГАЦИЯ отправлена в канал.\n\n"
            f"Теперь закрепите это сообщение в канале.",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка отправки в канал</b>\n\n"
            f"Ошибка: {str(e)}\n\n"
            "Проверьте:\n"
            "• Бот добавлен как администратор в канал\n"
            "• CHANNEL_ID указан правильно\n"
            "• Бот имеет права на отправку сообщений",
            parse_mode="HTML"
        )
        print(f"❌ Ошибка отправки mini app в канал: {e}")


# Обработчик для кнопки "Управление подпиской"
@dp.message(lambda m: (m.text or "").strip() == BTN_MANAGE_SUB)
async def manage_subscription(message: Message):
    """Обработчик кнопки управления подпиской - показывает меню с кнопкой отмены"""
    user_id = message.from_user.id
    await send_typing_action(message.chat.id)
    
    # Проверяем, есть ли активная подписка
    expires_at = await get_subscription_expires_at(user_id)
    now = datetime.utcnow()
    
    if not expires_at or expires_at <= now:
        await message.answer(
            "ℹ️ <b>У вас нет активной подписки</b>\n\n"
            "Подписка уже неактивна или отсутствует.",
            parse_mode="HTML",
            reply_markup=await main_menu(user_id)
        )
        return
    
    # Получаем информацию о подписке
    starts_at = await get_subscription_starts_at(user_id)
    starts_str = format_datetime_moscow(starts_at) if starts_at else "неизвестно"
    expires_str = format_datetime_moscow(expires_at)
    
    # Проверяем статус автопродления
    auto_renewal_enabled = await is_auto_renewal_enabled(user_id)
    auto_status = "✅ Включено" if auto_renewal_enabled else "❌ Отключено"
    
    # Показываем меню управления (динамически в зависимости от статуса автопродления)
    await message.answer(
        "⚙️ <b>Управление подпиской</b>\n\n"
        f"📅 <b>Активна с:</b> {starts_str}\n"
        f"📅 <b>Активна до:</b> {expires_str}\n\n"
        f"🔄 <b>Автопродление:</b> {auto_status}\n\n"
        "Выберите действие ниже 👇",
        parse_mode="HTML",
        reply_markup=await manage_subscription_menu(user_id)
    )


# Обработчик для кнопки "Назад в меню"
@dp.message(lambda m: (m.text or "").strip() == "◀️ Назад в меню")
async def back_to_main_menu(message: Message):
    """Возврат в главное меню"""
    await message.answer(
        "📋 <b>Главное меню</b>",
        parse_mode="HTML",
        reply_markup=await main_menu(message.from_user.id)
    )


# Обработчик для кнопки "Отменить подписку"
@dp.message(lambda m: (m.text or "").strip() == BTN_CANCEL_SUB)
async def cancel_subscription(message: Message):
    """Обработчик кнопки отмены подписки - отключает автопродление (карта НЕ отвязывается)"""
    user_id = message.from_user.id
    await send_typing_action(message.chat.id)
    
    # Проверяем, есть ли активная подписка
    expires_at = await get_subscription_expires_at(user_id)
    now = datetime.utcnow()
    
    if not expires_at or expires_at <= now:
        await message.answer(
            "ℹ️ <b>У вас нет активной подписки</b>\n\n"
            "Подписка уже неактивна или отсутствует.",
            parse_mode="HTML",
            reply_markup=await main_menu(user_id)
        )
        return
    
    # Отключаем только автопродление (карту НЕ отвязываем, чтобы можно было возобновить)
    await set_auto_renewal(user_id, False)
    
    # Получаем информацию о подписке
    expires_str = format_datetime_moscow(expires_at)
    
    # Показываем обновленное меню управления с кнопкой "Возобновить подписку"
    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⏸️ <b>Автопродление отключено</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ Автопродление подписки отключено.\n\n"
        f"📅 <b>Подписка действует до:</b> {expires_str}\n\n"
        "💡 После окончания срока подписка не будет продлеваться автоматически.\n\n"
        "🔄 Вы можете возобновить автопродление, нажав кнопку ниже 👇",
        parse_mode="HTML",
        reply_markup=await manage_subscription_menu(user_id)  # Показываем меню с кнопкой "Возобновить подписку"
    )


# Обработчик для кнопки "Возобновить подписку"
@dp.message(lambda m: (m.text or "").strip() == BTN_RESUME_SUB)
async def resume_subscription(message: Message):
    """Обработчик кнопки возобновления подписки - включает автопродление обратно"""
    user_id = message.from_user.id
    await send_typing_action(message.chat.id)
    
    # Проверяем, есть ли активная подписка
    expires_at = await get_subscription_expires_at(user_id)
    now = datetime.utcnow()
    
    if not expires_at or expires_at <= now:
        await message.answer(
            "ℹ️ <b>У вас нет активной подписки</b>\n\n"
            "Подписка уже неактивна или отсутствует.",
            parse_mode="HTML",
            reply_markup=await main_menu(user_id)
        )
        return
    
    # Проверяем, есть ли сохраненная карта
    saved_method = await get_saved_payment_method_id(user_id)
    
    if not saved_method:
        # Нет сохраненной карты - нельзя возобновить автопродление
        expires_str = format_datetime_moscow(expires_at)
        await message.answer(
            "⚠️ <b>Невозможно возобновить автопродление</b>\n\n"
            "У вас нет привязанной карты для автоматического списания.\n\n"
            f"📅 <b>Подписка действует до:</b> {expires_str}\n\n"
            "💡 Для возобновления автопродления необходимо:\n"
            "1️⃣ Оплатить подписку заново\n"
            "2️⃣ При оплате отметить галочку «Сохранить карту для следующих платежей»",
            parse_mode="HTML",
            reply_markup=await manage_subscription_menu(user_id)
        )
        return
    
    # Включаем автопродление обратно
    success = await set_auto_renewal(user_id, True)
    
    if success:
        expires_str = format_datetime_moscow(expires_at)
        # Следующее списание будет в день окончания подписки
        next_payment_str = format_datetime_moscow(expires_at)
        
        # Показываем обновленное меню управления с кнопкой "Отменить подписку"
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✅ <b>Автопродление возобновлено</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔄 Автопродление подписки включено.\n\n"
            f"📅 <b>Подписка действует до:</b> {expires_str}\n"
            f"💳 <b>Следующее списание:</b> {next_payment_str}\n\n"
            "✅ Подписка будет автоматически продлеваться каждые 30 дней.",
            parse_mode="HTML",
            reply_markup=await manage_subscription_menu(user_id)  # Показываем меню с кнопкой "Отменить подписку"
        )
    else:
        await message.answer(
            "❌ <b>Ошибка возобновления автопродления</b>\n\n"
            "Пожалуйста, попробуйте позже или обратитесь в поддержку: @otd_zabota",
            parse_mode="HTML",
            reply_markup=await manage_subscription_menu(user_id)
        )


# Старый обработчик автопродления - УДАЛЕН, заменен на новые обработчики выше
# @dp.message(lambda m: (m.text or "").strip().startswith("🔄 Автопродление"))
# async def auto_renewal_toggle_OLD(message: Message):
    """Обработчик кнопки автопродления подписки"""
    user_id = message.from_user.id
    await send_typing_action(message.chat.id)
    
    # Проверяем текущий статус автопродления
    current_status = await is_auto_renewal_enabled(user_id)
    
    if current_status:
        # Выключаем автопродление
        await set_auto_renewal(user_id, False)
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            "❌ <b>Автопродление выключено</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Ваша подписка не будет автоматически продлеваться.\n\n"
            "💡 Для включения нажмите кнопку автопродления ещё раз.",
            parse_mode="HTML",
            reply_markup=await main_menu(user_id)
        )
    else:
        # Проверяем, есть ли сохраненный метод оплаты
        saved_method = await get_saved_payment_method_id(user_id)
        
        if not saved_method:
            # Нет сохраненного метода оплаты
            await message.answer(
                "⚠️ <b>Карта не привязана</b>\n\n"
                "Для включения автопродления необходимо сохранить данные карты при оплате.\n\n"
                "📋 <b>Как включить автопродление:</b>\n"
                "1️⃣ Нажмите кнопку 💳 Оплатить подписку\n"
                "2️⃣ При оплате отметьте галочку «Сохранить карту для следующих платежей»\n"
                "3️⃣ После успешной оплаты нажмите кнопку автопродления ещё раз\n\n"
                "💡 После этого подписка будет продлеваться автоматически каждые 30 дней.",
                parse_mode="HTML",
                reply_markup=await main_menu(user_id)
            )
        else:
            # Включаем автопродление
            success = await set_auto_renewal(user_id, True)
            if success:
                # Получаем дату окончания текущей подписки для расчета следующего списания
                expires_at = await get_subscription_expires_at(user_id)
                
                if expires_at:
                    # Следующее списание будет в день окончания подписки (в момент окончания)
                    next_payment_date = expires_at
                    next_payment_str = format_datetime_moscow(next_payment_date)
                    
                    await message.answer(
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "✅ <b>Автопродление включено!</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"📅 <b>Следующее списание:</b> {next_payment_str}\n\n"
                        "🔄 Подписка будет автоматически продлеваться каждые 30 дней.\n\n"
                        "💳 Списывание происходит с сохранённой карты автоматически.",
                        parse_mode="HTML",
                        reply_markup=await main_menu(user_id)
                    )
                else:
                    await message.answer(
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "✅ <b>Автопродление включено!</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        "🔄 Подписка будет автоматически продлеваться каждые 30 дней при наличии активной подписки.\n\n"
                        "💳 Списывание происходит с сохранённой карты автоматически.",
                        parse_mode="HTML",
                        reply_markup=await main_menu(user_id)
                    )
            else:
                await message.answer(
                    "❌ <b>Ошибка включения автопродления</b>\n\n"
                    "Пожалуйста, попробуйте позже или обратитесь в поддержку: @otd_zabota",
                    parse_mode="HTML",
                    reply_markup=await main_menu(user_id)
                )


# Старый обработчик отвязки карты - УДАЛЕН, теперь используется через "Отключить подписку"
# Кнопка "Отвязать карту" больше не показывается в главном меню


@dp.chat_join_request()
async def approve_join_request(join_request: ChatJoinRequest):
    """
    Автоматически одобряет заявки на вступление от оплативших пользователей
    """
    if CHANNEL_ID and join_request.chat.id == CHANNEL_ID:
        user_id = join_request.from_user.id
        
        # Проверяем, оплатил ли пользователь (асинхронная версия)
        if await is_user_allowed(user_id):
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

