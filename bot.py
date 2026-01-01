import asyncio
import os
import inspect
import aiohttp
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ChatJoinRequest, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile, BufferedInputFile, ContentType, WebAppInfo, ChatMemberUpdated
from aiogram.enums import ChatMemberStatus
from aiogram.enums import ChatAction
from dotenv import load_dotenv

from telegram_utils import safe_send_message, safe_send_video

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
    is_bonus_week_active,
    get_current_subscription_price,
    get_current_subscription_duration,
    get_production_subscription_price,
    get_production_subscription_duration,
    get_bonus_week_start,
    get_bonus_week_end,
    dni_prazdnika,
    vremya_sms,
    BONUS_WEEK_PRICE_RUB,
)

def format_subscription_duration(days: float) -> str:
    """Форматирует длительность подписки: показывает минуты если < 1 дня, иначе дни"""
    if days < 1:
        minutes = int(days * 1440)
        if minutes == 1:
            return "1 минута"
        elif 2 <= minutes <= 4:
            return f"{minutes} минуты"
        else:
            return f"{minutes} минут"
    else:
        days_int = int(days)
        if days_int == 1:
            return "1 день"
        elif 2 <= days_int <= 4:
            return f"{days_int} дня"
        else:
            return f"{days_int} дней"


def ensure_timezone_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Приводит datetime к timezone-aware (UTC), если он timezone-naive"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

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


BTN_PAY_1 = "💳 Получить доступ"  # Показывается если нет подписки
BTN_MANAGE_SUB = "⚙️ Управление доступом"  # Показывается если есть подписка
BTN_CANCEL_SUB = "❌ Отменить доступ и отключить автопродление"  # Показывается в меню управления если автопродление включено
BTN_RESUME_SUB = "▶️ Возобновить доступ"  # Показывается в меню управления если автопродление отключено
BTN_STATUS_1 = "📊 Статус доступа"
BTN_ABOUT_1 = "ℹ️ О проекте"
BTN_CHECK_1 = "🔍 Проверить оплату"
BTN_SUPPORT = "💬 Поддержка"

# Кнопки для бонусной недели
BTN_BONUS_WEEK = "🎁 Бонус в честь запуска канала Наиля Хасанова"
BTN_BACK_TO_MENU = "◀️ Назад в меню"
BTN_DISABLE_AUTO_RENEWAL = "❌ Отказаться от автопродления"
BTN_REMOVE_CARD = "💳 Отвязать карту"


async def bonus_week_menu() -> ReplyKeyboardMarkup:
    """Создает меню для бонусной недели"""
    keyboard = [
        [KeyboardButton(text=BTN_BONUS_WEEK)],
        [KeyboardButton(text=BTN_ABOUT_1)],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
    )


async def main_menu(telegram_id: int = None) -> ReplyKeyboardMarkup:
    """Создает главное меню с учетом статуса доступа"""
    # Определяем, какая кнопка показывается: "Получить доступ" или "Управление доступом"
    if telegram_id:
        expires_at = await get_subscription_expires_at(telegram_id)
        from datetime import timezone
        now = datetime.now(timezone.utc)
        # Убеждаемся, что expires_at имеет timezone для сравнения
        expires_at = ensure_timezone_aware(expires_at)
        has_active_subscription = expires_at and expires_at > now
        
        # Проверяем, включено ли автопродление
        # Если автопродление отключено, показываем "Получить доступ" даже при активной подписке
        auto_renewal_enabled = await is_auto_renewal_enabled(telegram_id)
        # Показываем "Управление доступом" только если подписка активна И автопродление включено
        show_manage_button = has_active_subscription and auto_renewal_enabled
    else:
        show_manage_button = False
    
    # КРИТИЧЕСКИ ВАЖНО: Если активна бонусная неделя, но у пользователя есть активная подписка,
    # показываем меню с "Управление доступом", а не бонусное меню
    if is_bonus_week_active() and not show_manage_button:
        # Бонусная неделя активна, но у пользователя нет активной подписки - показываем бонусное меню
        return await bonus_week_menu()
    
    # Если есть активная подписка с автопродлением - показываем "Управление доступом", иначе "Получить доступ"
    payment_button = BTN_MANAGE_SUB if show_manage_button else BTN_PAY_1
    
    keyboard = [
        [KeyboardButton(text=payment_button)],
        [KeyboardButton(text=BTN_STATUS_1)],
    ]
    
    
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
    """Создает меню управления доступом с кнопкой отмены/возобновления в зависимости от статуса автопродления"""
    # Проверяем статус автопродления
    auto_renewal_enabled = await is_auto_renewal_enabled(telegram_id)
    
    # Если автопродление включено - показываем "Отменить доступ", иначе "Возобновить доступ"
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
    # Приоритет: 1) локальный файл Video_nail_hasanov, 2) WELCOME_VIDEO_PATH, 3) WELCOME_VIDEO_URL
    VIDEO_RECORDING_PATH = os.path.join(os.path.dirname(__file__), "Video_nail_hasanov.mp4")
    VIDEO_PATH = os.getenv("WELCOME_VIDEO_PATH", "/opt/bot_telegram/welcome_video.mp4")
    VIDEO_GIF_PATH = os.getenv("WELCOME_VIDEO_GIF_PATH", "/opt/bot_telegram/welcome_video.gif")  # GIF для авто-воспроизведения
    VIDEO_URL = os.getenv("WELCOME_VIDEO_URL", None)  # Можно указать URL видео
    
    # Приоритет: сначала пробуем локальный файл (быстрее), потом URL
    
    # Текст приветственного сообщения
    if is_bonus_week_active():
        # Текст для бонусной недели
        # Вычисляем реальное оставшееся время до конца бонусной недели
        from datetime import timezone
        now = datetime.now(timezone.utc)
        bonus_end = get_bonus_week_end()
        time_until_bonus_end = bonus_end - now
        
        # Форматируем оставшееся время
        if time_until_bonus_end.total_seconds() > 0:
            days_left = time_until_bonus_end.days
            hours_left = int((time_until_bonus_end.total_seconds() % 86400) / 3600)
            minutes_left = int((time_until_bonus_end.total_seconds() % 3600) / 60)
            
            if days_left > 0:
                time_left_text = f"{days_left} день{'а' if 2 <= days_left <= 4 else 'ей'}"
            elif hours_left > 0:
                time_left_text = f"{hours_left} час{'а' if 2 <= hours_left <= 4 else 'ов'}"
            else:
                time_left_text = f"{minutes_left} минут{'ы' if 2 <= minutes_left <= 4 else ''}"
        else:
            time_left_text = "завершилась"
        
        # Форматируем время начала и окончания бонусной недели
        bonus_start = get_bonus_week_start()
        # Убеждаемся, что datetime имеет timezone для правильного форматирования
        if bonus_start.tzinfo is None:
            from datetime import timezone
            bonus_start = bonus_start.replace(tzinfo=timezone.utc)
        if bonus_end.tzinfo is None:
            from datetime import timezone
            bonus_end = bonus_end.replace(tzinfo=timezone.utc)
        bonus_start_moscow = format_datetime_moscow(bonus_start)
        bonus_end_moscow = format_datetime_moscow(bonus_end)
        
        welcome_text = (
            "👋 <b>Добро пожаловать!</b>\n\n"
            "Меня зовут Наиль Хасанов, и я рад приветствовать вас в нашем боте.\n\n"
            "🎉 <b>БОНУСНАЯ НЕДЕЛЯ В ЧЕСТЬ ЗАПУСКА КАНАЛА!</b>\n\n"
            f"🎁 В честь открытия канала Наиля Хасанова мы дарим вам <b>бонусную неделю</b>!\n\n"
            f"💰 <b>Специальное предложение:</b>\n"
            f"• Доступ к закрытому каналу всего за <b>1 рубль</b>\n"
            f"• Бонусная неделя началась: <b>{bonus_start_moscow}</b>\n"
            f"• Бонусная неделя закончится: <b>{bonus_end_moscow}</b>\n"
            f"• До окончания бонусной недели осталось: <b>{time_left_text}</b>\n\n"
            f"🔄 <b>После окончания бонусной недели:</b>\n"
            f"• Произойдет автоматическое продление на полную стоимость доступа\n"
            f"• Стоимость: <b>2990 рублей</b>\n"
            f"• Срок доступа: <b>30 дней</b>\n\n"
            f"⚙️ <b>Важно:</b> Автопродление можно будет отключить в любой момент в меню «Управление доступом».\n\n"
            f"⏰ <b>Бонусная неделя действует ограниченное время!</b>\n\n"
            "Выберите действие в меню ниже 👇"
        )
    else:
        # Обычный текст для продакшн режима
        welcome_text = (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Меня зовут Наиль Хасанов, и я рад приветствовать вас в нашем боте.\n\n"
        "🎯 Здесь вы можете:\n"
        "• Получить доступ к закрытому каналу\n"
            "• Управлять своим доступом\n"
        "• Настроить автопродление\n\n"
        "Выберите действие в меню ниже 👇"
    )
    
    # Отправляем видео с текстом в caption (встроено в сообщение)
    video_sent = False

    # ПРИОРИТЕТ 1: Сначала пробуем файл Video_nail_hasanov
    if os.path.exists(VIDEO_RECORDING_PATH):
        try:
            file_size = os.path.getsize(VIDEO_RECORDING_PATH)
            file_size_mb = file_size / 1024 / 1024
            print(f"📹 Найден файл Video_nail_hasanov, размер: {file_size_mb:.1f}MB")
            
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
                # Пытаемся получить метаданные видео для лучшего отображения
                width = None
                height = None
                duration = None
                
                try:
                    # Пробуем использовать ffprobe для получения метаданных (если установлен)
                    import subprocess
                    result = subprocess.run(
                        ['ffprobe', '-v', 'error', '-select_streams', 'v:0', 
                         '-show_entries', 'stream=width,height,duration', 
                         '-of', 'json', VIDEO_RECORDING_PATH],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        import json
                        data = json.loads(result.stdout)
                        if 'streams' in data and len(data['streams']) > 0:
                            stream = data['streams'][0]
                            width = int(stream.get('width', 0))
                            height = int(stream.get('height', 0))
                            duration = float(stream.get('duration', 0))
                            print(f"📐 Получены метаданные: {width}x{height}, длительность: {duration:.1f}с")
                except Exception as meta_error:
                    # Если ffprobe не установлен или произошла ошибка, используем значения по умолчанию
                    print(f"ℹ️ Не удалось получить метаданные через ffprobe: {meta_error}")
                    # Для вертикального видео (9:16) используем стандартные размеры
                    # Это может помочь видео открываться на полный экран в мобильных клиентах
                    width = 1080
                    height = 1920
                
                # Отправляем видео с метаданными для лучшего отображения
                video_params = {
                    "chat_id": message.chat.id,
                    "video": video_file,
                    "caption": welcome_text,
                    "parse_mode": "HTML",
                    "supports_streaming": True,  # Включаем потоковое воспроизведение
                    "reply_markup": await main_menu(message.from_user.id),
                }
                
                # Добавляем метаданные, если они доступны
                if width and height:
                    video_params["width"] = width
                    video_params["height"] = height
                if duration:
                    video_params["duration"] = int(duration)
                
                await safe_send_video(
                    bot=bot,
                    chat_id=message.chat.id,
                    video=video_file,
                    caption=welcome_text,
                    parse_mode="HTML",
                    reply_markup=await main_menu(message.from_user.id),
                    width=width,
                    height=height,
                    duration=int(duration) if duration else None
                )
                print(f"✅ Видео успешно отправлено: {VIDEO_RECORDING_PATH}")
            video_sent = True
            return  # Прерываем выполнение
        except Exception as e:
            print(f"⚠️ Ошибка отправки Video_nail_hasanov: {e}")
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
                    
                    await safe_send_video(
                        bot=bot,
                        chat_id=message.chat.id,
                        video=video_file,
                        caption=welcome_text,
                        parse_mode="HTML",
                        reply_markup=await main_menu(message.from_user.id),
                        width=width,
                        height=height,
                        duration=duration
                    )
                    print(f"✅ Видео успешно отправлено из файла: {VIDEO_PATH}")
                except Exception as meta_error:
                    # Если не удалось получить метаданные, отправляем без них
                    print(f"⚠️ Не удалось получить метаданные видео: {meta_error}, отправляю без них")
                    await safe_send_video(
                        bot=bot,
                        chat_id=message.chat.id,
                        video=video_file,
                        caption=welcome_text,
                        parse_mode="HTML",
                        reply_markup=await main_menu(message.from_user.id)
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
            "❌ <b>Доступ не активен</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 Для получения доступа нажмите кнопку 💳 Получить доступ",
            parse_mode="HTML"
        )
        return

    now = datetime.now(timezone.utc)
    expires_at = ensure_timezone_aware(expires_at)
    if expires_at and expires_at > now:
        starts_at = await get_subscription_starts_at(message.from_user.id)
        if starts_at:
            starts_str = format_datetime_moscow(starts_at)
            expires_str = format_datetime_moscow(expires_at)
            await message.answer(
                "━━━━━━━━━━━━━━━━━━━━\n"
                "✅ <b>Доступ активен</b>\n"
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
                f"✅ <b>Доступ активен</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 <b>Активна до:</b> {expires_str}",
                parse_mode="HTML"
            )
    else:
        expires_str = format_datetime_moscow(expires_at)
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ <b>Доступ закончился</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 <b>Закончилась:</b> {expires_str}\n\n"
            "💡 Для продления доступа нажмите кнопку 💳 Получить доступ",
            parse_mode="HTML"
        )


@dp.message(lambda m: (m.text or "").strip() == BTN_ABOUT_1)
async def about(message: Message):
    # КРИТИЧЕСКИ ВАЖНО: Очищаем кэш перед проверкой меню, чтобы получить актуальные данные
    # Используем main_menu, которая правильно проверяет наличие активной подписки
    # и показывает "Управление доступом" если подписка активна, даже во время бонусной недели
    from db import _clear_cache
    _clear_cache()
    
    await message.answer(
        "📖 <b>О проекте</b>\n\n"
        "Это бот для доступа к закрытому Telegram-каналу.\n\n"
        "🔐 <b>Безопасность:</b>\n"
        "• Оплата через ЮKassa\n"
        "• Защищенные платежи\n"
        "• Автоматическое управление доступом\n\n"
        "✨ <b>Удобство:</b>\n"
        "• Простая оплата\n"
        "• Автопродление доступа\n"
        "• Мгновенный доступ к контенту",
        parse_mode="HTML",
        reply_markup=await main_menu(message.from_user.id)
    )


@dp.message(lambda m: (m.text or "").strip() == BTN_BONUS_WEEK)
async def bonus_week_info(message: Message):
    """Обработчик кнопки 'Бонус в честь запуск канала Наиля Хасанова'"""
    await ensure_user(message.from_user.id, message.from_user.username)
    await send_typing_action(message.chat.id)
    
    if not is_bonus_week_active():
        # Если бонусная неделя закончилась, показываем обычное меню
        await message.answer(
            "ℹ️ <b>Бонусная неделя закончилась</b>\n\n"
            "Бонусное предложение больше не доступно. Вы можете получить доступ по обычной стоимости.",
            parse_mode="HTML",
            reply_markup=await main_menu(message.from_user.id)
        )
        return
    
    # Вычисляем реальное оставшееся время до конца бонусной недели
    from datetime import timezone
    now = datetime.now(timezone.utc)
    bonus_end = get_bonus_week_end()
    time_until_bonus_end = bonus_end - now
    
    # Форматируем оставшееся время
    if time_until_bonus_end.total_seconds() > 0:
        days_left = time_until_bonus_end.days
        hours_left = int((time_until_bonus_end.total_seconds() % 86400) / 3600)
        minutes_left = int((time_until_bonus_end.total_seconds() % 3600) / 60)
        
        if days_left > 0:
            time_left_text = f"{days_left} день{'а' if 2 <= days_left <= 4 else 'ей'}"
        elif hours_left > 0:
            time_left_text = f"{hours_left} час{'а' if 2 <= hours_left <= 4 else 'ов'}"
        else:
            time_left_text = f"{minutes_left} минут{'ы' if 2 <= minutes_left <= 4 else ''}"
    else:
        time_left_text = "завершилась"
    
    # Форматируем время начала и окончания бонусной недели
    bonus_start = get_bonus_week_start()
    # Убеждаемся, что datetime имеет timezone для правильного форматирования
    if bonus_start.tzinfo is None:
        from datetime import timezone
        bonus_start = bonus_start.replace(tzinfo=timezone.utc)
    if bonus_end.tzinfo is None:
        from datetime import timezone
        bonus_end = bonus_end.replace(tzinfo=timezone.utc)
    bonus_start_moscow = format_datetime_moscow(bonus_start)
    bonus_end_moscow = format_datetime_moscow(bonus_end)
    
    bonus_text = (
        "🎉 <b>БОНУСНАЯ НЕДЕЛЯ В ЧЕСТЬ ЗАПУСКА КАНАЛА!</b>\n\n"
        "🎁 В честь открытия канала Наиля Хасанова мы дарим вам <b>бонусную неделю</b>!\n\n"
        "💰 <b>Специальное предложение:</b>\n"
        f"• Доступ к закрытому каналу всего за <b>1 рубль</b>\n"
        f"• Бонусная неделя началась: <b>{bonus_start_moscow}</b>\n"
        f"• Бонусная неделя закончится: <b>{bonus_end_moscow}</b>\n"
        f"• До окончания бонусной недели осталось: <b>{time_left_text}</b>\n\n"
        "🔄 <b>После окончания бонусной недели:</b>\n"
        "• Произойдет автоматическое продление на полную стоимость доступа\n"
        "• Стоимость: <b>2990 рублей</b>\n"
        "• Срок доступа: <b>30 дней</b>\n\n"
        "⚙️ <b>Важно:</b> Автопродление можно будет отключить в любой момент в меню «Управление доступом».\n\n"
        "⏰ <b>Бонусная неделя действует ограниченное время!</b>\n\n"
        "Нажмите кнопку ниже, чтобы получить доступ за 1 рубль 👇"
    )
    
    # Сразу создаем платеж и показываем ссылку на оплату
    # Проверяем активный pending платеж
    active_payment = await get_active_pending_payment(message.from_user.id, minutes=PAYMENT_LINK_VALID_MINUTES)
    
    pay_url = None
    payment_id = None
    
    if active_payment:
        # Используем существующий платеж
        payment_id, created_at = active_payment
        pay_url = await maybe_await(get_payment_url, payment_id)
    else:
        # Создаем новый платеж для бонусной недели
        return_url_with_user = get_return_url(message.from_user.id)
        bonus_duration_days = dni_prazdnika / 1440  # Конвертируем минуты в дни
        
        payment_id, pay_url = await maybe_await(
            create_payment,
            amount_rub=BONUS_WEEK_PRICE_RUB,
            description=f"Бонусная неделя: Доступ к каналу ({format_subscription_duration(bonus_duration_days)})",
            return_url=return_url_with_user,
            customer_email=CUSTOMER_EMAIL,
            telegram_user_id=message.from_user.id,
            enable_save_payment_method=True,
        )
        
        await save_payment(message.from_user.id, payment_id, status="pending")
    
    # Если URL не получен, отправляем сообщение об ошибке
    if not pay_url:
        await message.answer(
            "❌ <b>Ошибка создания платежа</b>\n\n"
            "Не удалось создать ссылку на оплату. Пожалуйста, попробуйте позже.",
            parse_mode="HTML",
            reply_markup=await bonus_week_menu()
        )
        return
    
    # Создаем кнопку с прямой ссылкой на оплату (URL, а не callback)
    pay_button = InlineKeyboardButton(text="💳 Оплатить 1₽", url=pay_url)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[pay_button]])
    
    # Оставляем ПЕРВОЕ уведомление (bonus_text) с добавленной кнопкой оплаты
    await message.answer(
        bonus_text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


@dp.callback_query(lambda c: c.data == "bonus_week_pay")
async def bonus_week_pay_callback(callback: CallbackQuery):
    """Обработчик нажатия на кнопку оплаты в бонусной неделе"""
    await callback.answer()
    # Используем callback.message для редактирования исходного сообщения
    # и передаем флаг, что это callback, чтобы не дублировать сообщения
    await bonus_week_pay(callback.message, is_callback=True)


@dp.callback_query(lambda c: c.data == "back_to_bonus_menu")
async def back_to_bonus_menu_callback(callback: CallbackQuery):
    """Обработчик кнопки 'Назад в меню' в бонусной неделе"""
    await callback.answer()
    user_id = callback.from_user.id
    if is_bonus_week_active():
        # В бонусной неделе проверяем, есть ли активная подписка
        from db import get_subscription_expires_at
        from datetime import timezone
        expires_at = await get_subscription_expires_at(user_id)
        now = datetime.now(timezone.utc)
        expires_at = ensure_timezone_aware(expires_at)
        has_active = expires_at and expires_at > now
        
        if has_active:
            # Если есть активная подписка, показываем меню с "Управление доступом"
            BTN_MANAGE_SUB = "⚙️ Управление доступом"
            BTN_ABOUT_1 = "ℹ️ О проекте"
            from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
            keyboard = [
                [KeyboardButton(text=BTN_MANAGE_SUB)],
                [KeyboardButton(text=BTN_ABOUT_1)],
            ]
            menu = ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
            await callback.message.answer(
                "Вы вернулись в главное меню",
                reply_markup=menu
            )
        else:
            # Если нет активной подписки, показываем бонусное меню
            await callback.message.answer(
                "Вы вернулись в главное меню",
                reply_markup=await bonus_week_menu()
            )
    else:
        await callback.message.answer(
            "Вы вернулись в главное меню",
            reply_markup=await main_menu(user_id)
        )


async def bonus_week_pay(message: Message, is_callback: bool = False):
    """Логика оплаты в бонусной неделе
    
    Args:
        message: Сообщение или callback message
        is_callback: Если True, редактируем исходное сообщение вместо отправки нового
    """
    await ensure_user(message.from_user.id, message.from_user.username)
    if not is_callback:
        await send_typing_action(message.chat.id)
    
    if not is_bonus_week_active():
        await message.answer(
            "ℹ️ <b>Бонусная неделя закончилась</b>\n\n"
            "Бонусное предложение больше не доступно.",
            parse_mode="HTML",
            reply_markup=await main_menu(message.from_user.id)
        )
        return
    
    # Проверяем активную подписку
    expires_at = await get_subscription_expires_at(message.from_user.id)
    
    now = datetime.now(timezone.utc)
    expires_at = ensure_timezone_aware(expires_at)
    if expires_at and expires_at > now:
        # У пользователя уже есть активная подписка
        starts_at = await get_subscription_starts_at(message.from_user.id)
        starts_str = format_datetime_moscow(starts_at) if starts_at else "неизвестно"
        expires_str = format_datetime_moscow(expires_at)
        
        auto_renewal_enabled = await is_auto_renewal_enabled(message.from_user.id)
        
        if auto_renewal_enabled:
            management_text = f"⚙️ Для управления доступом нажмите кнопку «{BTN_MANAGE_SUB}»"
        else:
            management_text = "💡 Оплатить доступ вы сможете после окончания вашей подписки"
        
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Доступ уже активирован!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 <b>Активна с:</b> {starts_str}\n"
            f"📅 <b>Активна до:</b> {expires_str}\n\n"
            f"💬 Если у вас нет доступа к платному каналу, обратитесь к менеджеру: @otd_zabota\n\n"
            f"{management_text}",
            parse_mode="HTML",
            reply_markup=await bonus_week_menu()
        )
        return
    
    # Проверяем активный pending платеж
    active_payment = await get_active_pending_payment(message.from_user.id, minutes=PAYMENT_LINK_VALID_MINUTES)
    
    pay_url = None
    payment_id = None
    
    if active_payment:
        # Используем существующий платеж
        payment_id, created_at = active_payment
        pay_url = await maybe_await(get_payment_url, payment_id)
    else:
        # Создаем новый платеж для бонусной недели
        return_url_with_user = get_return_url(message.from_user.id)
        bonus_duration_days = dni_prazdnika / 1440  # Конвертируем минуты в дни
        
        payment_id, pay_url = await maybe_await(
            create_payment,
            amount_rub=BONUS_WEEK_PRICE_RUB,
            description=f"Бонусная неделя: Доступ к каналу ({format_subscription_duration(bonus_duration_days)})",
            return_url=return_url_with_user,
            customer_email=CUSTOMER_EMAIL,
            telegram_user_id=message.from_user.id,
            enable_save_payment_method=True,
        )
        
        await save_payment(message.from_user.id, payment_id, status="pending")
    
    # Если URL не получен, отправляем сообщение об ошибке
    if not pay_url:
        await message.answer(
            "❌ <b>Ошибка создания платежа</b>\n\n"
            "Не удалось создать ссылку на оплату. Пожалуйста, попробуйте позже.",
            parse_mode="HTML",
            reply_markup=await bonus_week_menu()
        )
        return
    
    # Формируем текст с предупреждением о бонусной неделе
    bonus_duration_days = dni_prazdnika / 1440  # Конвертируем минуты в дни
    bonus_duration_text = f"{dni_prazdnika} минут" if dni_prazdnika < 60 else f"{dni_prazdnika // 60} час{'а' if 2 <= dni_prazdnika // 60 <= 4 else 'ов'}"
    
    pay_button = InlineKeyboardButton(text="💳 Оплатить 1₽", url=pay_url)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[pay_button]])
    
    subscription_text = (
        "🎉 <b>БОНУСНАЯ НЕДЕЛЯ: Оформление доступа</b>\n\n"
        f"💎 <b>Стоимость:</b> {format_subscription_duration(bonus_duration_days)} — 1 рубль\n\n"
        f"⏰ <b>Срок доступа:</b> {bonus_duration_text}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ <b>ВАЖНО:</b> После окончания бонусной недели:\n"
        "• Ваш доступ в канал закончится\n"
        "• Будет автоматически списана полная стоимость: <b>2990 рублей на 30 дней</b>\n"
        "• Автопродление можно отключить в меню «Управление доступом»\n\n"
        "💳 <b>Сохранение карты:</b>\n"
        "На форме оплаты вам будет предложено сохранить данные карты для автопродления.\n"
        "Вы можете выбрать, сохранять карту или нет.\n\n"
        "📋 Нажимая кнопку оплаты, вы соглашаетесь с:\n"
        "• Обработкой <a href=\"https://disk.yandex.ru/i/QadGJAMYKqbKpQ\">персональных данных</a>\n"
        "• Условиями <a href=\"https://disk.yandex.ru/i/fXUDJfj_i5cYIA\">публичной оферты</a>\n"
        "• Регулярными списаниями при включенном автопродлении\n\n"
        "🎁 После оплаты вы получите доступ к закрытому каналу"
    )
    
    # Если это callback, редактируем исходное сообщение с новым текстом и кнопкой с URL
    if is_callback:
        try:
            await message.edit_text(
                subscription_text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception as e:
            # Если не удалось отредактировать, отправляем новое сообщение
            await message.answer(
                subscription_text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
    else:
        await message.answer(
            subscription_text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True
        )


# Обработчик для кнопки "Получить доступ" (когда нет подписки)
@dp.message(lambda m: (m.text or "").strip() == BTN_PAY_1)
async def pay(message: Message):
    await ensure_user(message.from_user.id, message.from_user.username)
    await send_typing_action(message.chat.id)

    # ПЕРВЫМ ДЕЛОМ проверяем активную подписку
    expires_at = await get_subscription_expires_at(message.from_user.id)
    
    now = datetime.now(timezone.utc)
    expires_at = ensure_timezone_aware(expires_at)
    if expires_at and expires_at > now:
        starts_at = await get_subscription_starts_at(message.from_user.id)
        starts_str = format_datetime_moscow(starts_at) if starts_at else "неизвестно"
        expires_str = format_datetime_moscow(expires_at)
        
        # Проверяем, включено ли автопродление
        auto_renewal_enabled = await is_auto_renewal_enabled(message.from_user.id)
        
        # Формируем текст в зависимости от статуса автопродления
        if auto_renewal_enabled:
            # Если автопродление включено - показываем кнопку управления
            management_text = f"⚙️ Для управления доступом нажмите кнопку «{BTN_MANAGE_SUB}»"
        else:
            # Если автопродление отключено - сообщаем, что оплата доступна после окончания подписки
            management_text = "💡 Оплатить доступ вы сможете после окончания вашей подписки"
        
        await message.answer(
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Доступ уже активирован!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 <b>Активна с:</b> {starts_str}\n"
            f"📅 <b>Активна до:</b> {expires_str}\n\n"
            f"💬 Если у вас нет доступа к платному каналу, обратитесь к менеджеру: @otd_zabota\n\n"
            f"{management_text}",
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
        description=f"Доступ к каналу ({format_subscription_duration(SUBSCRIPTION_DAYS)})",
        return_url=return_url_with_user,
        customer_email=CUSTOMER_EMAIL,
        telegram_user_id=message.from_user.id,  # ✅ КРИТИЧНО
        enable_save_payment_method=True,  # Пытаемся включить сохранение способа оплаты
    )

    await save_payment(message.from_user.id, payment_id, status="pending")

    # Правильное склонение для рублей (используем один раз для кнопки и сообщения)
    amount_float = float(PAYMENT_AMOUNT_RUB)
    if amount_float == 1:
        ruble_text = "рубль"
        ruble_text_btn = "1₽"
    elif 2 <= amount_float <= 4 or (amount_float % 10 >= 2 and amount_float % 10 <= 4 and amount_float % 100 not in [12, 13, 14]):
        ruble_text = "рубля"
        ruble_text_btn = f"{int(amount_float)}₽"
    else:
        ruble_text = "рублей"
        ruble_text_btn = f"{int(amount_float)}₽"

    # Создаем кнопку оплаты с URL
    pay_button = InlineKeyboardButton(text=f"💳 Оплатить {ruble_text_btn}", url=pay_url)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[pay_button]])

    subscription_text = (
        "💰 <b>Оформление доступа</b>\n\n"
        f"💎 <b>Стоимость:</b> {format_subscription_duration(SUBSCRIPTION_DAYS)} — {PAYMENT_AMOUNT_RUB} {ruble_text}\n\n"
        f"🔄 <b>Автопродление:</b> каждые {format_subscription_duration(SUBSCRIPTION_DAYS)}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💳 <b>Сохранение карты:</b>\n"
        "На форме оплаты вам будет предложено сохранить данные карты для автопродления.\n"
        "Вы можете выбрать, сохранять карту или нет.\n\n"
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
                "💡 Сначала нажмите 💳 Получить доступ, чтобы создать платеж.",
            parse_mode="HTML"
        )
        return

    status = await maybe_await(get_payment_status, payment_id)
    await update_payment_status(payment_id, status)

    if status == "succeeded":
        # НЕ активируем подписку заново - только показываем существующие данные
        # Активация подписки происходит в webhook при успешной оплате
        starts_at = await get_subscription_starts_at(message.from_user.id)
        expires_at = await get_subscription_expires_at(message.from_user.id)
        
        if starts_at and expires_at:
            starts_str = format_datetime_moscow(starts_at)
            expires_str = format_datetime_moscow(expires_at)
            await message.answer(
                "━━━━━━━━━━━━━━━━━━━━\n"
                "✅ <b>Оплата подтверждена!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📅 <b>Доступ активен с:</b> {starts_str}\n"
                f"📅 <b>Доступ активен до:</b> {expires_str}\n\n"
                "🎉 <b>Ссылка на канал должна прийти в ближайшее время!</b>\n"
                "💬 Если ссылка не пришла, обратитесь в поддержку: @otd_zabota",
                parse_mode="HTML"
            )
        else:
            # Если подписка еще не активирована через webhook, сообщаем об этом
            await message.answer(
                "━━━━━━━━━━━━━━━━━━━━\n"
                "⏳ <b>Платёж обрабатывается</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Оплата успешна, но подписка еще активируется.\n\n"
                "💡 Подождите 1-2 минуты и нажмите эту кнопку ещё раз.\n"
                "💬 Если проблема сохраняется, обратитесь в поддержку: @otd_zabota",
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
            "🔄 Попробуйте оплатить снова, нажав кнопку 💳 Получить доступ",
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


# Обработчик кнопки "Ссылка на канал" удален - кнопка больше не используется


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
        # Для каналов используем обычную URL кнопку вместо WebApp
        # WebApp кнопки могут не поддерживаться в каналах
        # URL кнопка откроет mini app в браузере/приложении Telegram
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="Навигация",
                    url=mini_app_url  # Используем url вместо web_app
                )
            ]]
        )
        
        # Отправляем сообщение с кнопкой в канал
        sent_message = await bot.send_message(
            chat_id=CHANNEL_ID,
            text="🔥",
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


# Обработчик для кнопки "Управление доступом"
@dp.message(lambda m: (m.text or "").strip() == BTN_MANAGE_SUB)
async def manage_subscription(message: Message):
    """Обработчик кнопки управления доступом - показывает меню с кнопкой отмены"""
    user_id = message.from_user.id
    await send_typing_action(message.chat.id)
    
    # Проверяем, есть ли активная подписка
    expires_at = await get_subscription_expires_at(user_id)
    from datetime import timezone
    now = datetime.now(timezone.utc)  # Используем timezone-aware datetime для правильного расчета
    
    if not expires_at or expires_at <= now:
        await message.answer(
            "ℹ️ <b>У вас нет активного доступа</b>\n\n"
            "Доступ уже неактивен или отсутствует.",
            parse_mode="HTML",
            reply_markup=await main_menu(user_id) if not is_bonus_week_active() else await bonus_week_menu()
        )
        return
    
    # Получаем информацию о подписке
    starts_at = await get_subscription_starts_at(user_id)
    starts_str = format_datetime_moscow(starts_at) if starts_at else "неизвестно"
    expires_str = format_datetime_moscow(expires_at)
    
    # Проверяем статус автопродления
    auto_renewal_enabled = await is_auto_renewal_enabled(user_id)
    auto_status = "✅ Включено" if auto_renewal_enabled else "❌ Отключено"
    
    # Проверяем, активна ли бонусная неделя и является ли подписка бонусной
    is_bonus = is_bonus_week_active()
    bonus_week_end = get_bonus_week_end()
    
    # Вычисляем остаток времени до окончания бонусной недели (в реальном времени)
    if is_bonus and expires_at <= bonus_week_end:
        # Это подписка из бонусной недели
        # ВАЖНО: Используем текущее время для расчета оставшегося времени
        now_real = datetime.now(timezone.utc)  # Получаем актуальное время каждый раз
        time_until_bonus_end = bonus_week_end - now_real
        if time_until_bonus_end.total_seconds() > 0:
            days_left = time_until_bonus_end.days
            hours_left = int((time_until_bonus_end.total_seconds() % 86400) / 3600)
            minutes_left = int((time_until_bonus_end.total_seconds() % 3600) / 60)
            
            if days_left > 0:
                time_left_text = f"{days_left} день{'а' if 2 <= days_left <= 4 else 'ей'}"
            elif hours_left > 0:
                time_left_text = f"{hours_left} час{'а' if 2 <= hours_left <= 4 else 'ов'}"
            else:
                time_left_text = f"{minutes_left} минут{'ы' if 2 <= minutes_left <= 4 else ''}"
            
            # Формируем текст для бонусной недели
            bonus_warning = (
                f"\n\n🎉 <b>БОНУСНАЯ НЕДЕЛЯ</b>\n"
                f"⏰ До окончания бонусной недели осталось: <b>{time_left_text}</b>\n\n"
            )
            
            if auto_renewal_enabled:
                bonus_warning += (
                    "⚠️ <b>После окончания бонусной недели:</b>\n"
                    "• Будет автоматически списана полная стоимость: <b>2990 рублей на 30 дней</b>\n"
                    "• Автопродление можно отключить до окончания бонусной недели\n\n"
                )
            else:
                bonus_warning += (
                    "⚠️ <b>Автопродление отключено</b>\n"
                    "• После окончания бонусной недели доступ в канал закончится\n"
                    "• Вы не будете удалены из канала до окончания бонусной недели\n\n"
                )
            
            management_text = (
                "⚙️ <b>Управление доступом (Бонусная неделя)</b>\n\n"
                f"📅 <b>Активна с:</b> {starts_str}\n"
                f"📅 <b>Активна до:</b> {expires_str}\n\n"
                f"🔄 <b>Автопродление:</b> {auto_status}\n"
                f"{bonus_warning}"
            )
            
            # Создаем меню для бонусной недели
            # Должно быть две кнопки: "Отказаться от автопродления" и "Назад в меню"
            if auto_renewal_enabled:
                # Показываем кнопки: отключить автопродление и назад в меню
                keyboard = ReplyKeyboardMarkup(
                    keyboard=[
                        [KeyboardButton(text=BTN_DISABLE_AUTO_RENEWAL)],
                        [KeyboardButton(text=BTN_BACK_TO_MENU)]
                    ],
                    resize_keyboard=True
                )
            else:
                # Автопродление отключено - показываем только кнопку "Назад в меню"
                keyboard = ReplyKeyboardMarkup(
                    keyboard=[
                        [KeyboardButton(text=BTN_BACK_TO_MENU)]
                    ],
                    resize_keyboard=True
                )
            
            await message.answer(
                management_text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return
    
    # Обычное управление доступом (продакшн режим)
    await message.answer(
        "⚙️ <b>Управление доступом</b>\n\n"
        f"📅 <b>Активна с:</b> {starts_str}\n"
        f"📅 <b>Активна до:</b> {expires_str}\n\n"
        f"🔄 <b>Автопродление:</b> {auto_status}\n\n"
        "Выберите действие ниже 👇",
        parse_mode="HTML",
        reply_markup=await manage_subscription_menu(user_id)
    )


# Обработчик для кнопки "Назад в меню"
@dp.message(lambda m: (m.text or "").strip() == "◀️ Назад в меню" or (m.text or "").strip() == BTN_BACK_TO_MENU)
async def back_to_main_menu(message: Message):
    """Возврат в главное меню"""
    user_id = message.from_user.id
    if is_bonus_week_active():
        # В бонусной неделе проверяем, есть ли активная подписка
        from db import get_subscription_expires_at
        from datetime import timezone
        expires_at = await get_subscription_expires_at(user_id)
        now = datetime.now(timezone.utc)
        expires_at = ensure_timezone_aware(expires_at)
        has_active = expires_at and expires_at > now
        
        if has_active:
            # Если есть активная подписка, показываем меню с "Управление доступом"
            BTN_MANAGE_SUB = "⚙️ Управление доступом"
            BTN_ABOUT_1 = "ℹ️ О проекте"
            from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
            keyboard = [
                [KeyboardButton(text=BTN_MANAGE_SUB)],
                [KeyboardButton(text=BTN_ABOUT_1)],
            ]
            menu = ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
            await message.answer(
                "📋 <b>Главное меню</b>",
                parse_mode="HTML",
                reply_markup=menu
            )
        else:
            # Если нет активной подписки, показываем бонусное меню
            await message.answer(
                "📋 <b>Главное меню</b>",
                parse_mode="HTML",
                reply_markup=await bonus_week_menu()
            )
    else:
        await message.answer(
            "📋 <b>Главное меню</b>",
            parse_mode="HTML",
            reply_markup=await main_menu(user_id)
    )


# Обработчик для кнопки "Отказаться от автопродления" (в бонусной неделе)
@dp.message(lambda m: (m.text or "").strip() == BTN_DISABLE_AUTO_RENEWAL)
async def disable_auto_renewal_bonus_week(message: Message):
    """Отключение автопродления в бонусной неделе"""
    user_id = message.from_user.id
    await send_typing_action(message.chat.id)
    
    if not is_bonus_week_active():
        await message.answer(
            "ℹ️ <b>Бонусная неделя закончилась</b>",
            parse_mode="HTML",
            reply_markup=await main_menu(user_id)
        )
        return
    
    expires_at = await get_subscription_expires_at(user_id)
    now = datetime.now(timezone.utc)
    
    if not expires_at or expires_at <= now:
        await message.answer(
            "ℹ️ <b>У вас нет активного доступа</b>",
            parse_mode="HTML",
            reply_markup=await bonus_week_menu()
        )
        return
    
    # Отключаем автопродление И отвязываем карту
    await set_auto_renewal(user_id, False)
    
    # ВАЖНО: Отвязываем карту (удаляем payment_method_id)
    card_removed = await delete_payment_method(user_id)
    
    expires_str = format_datetime_moscow(expires_at)
    bonus_week_end = get_bonus_week_end()
    time_until_bonus_end = bonus_week_end - now
    
    if time_until_bonus_end.total_seconds() > 0:
        days_left = time_until_bonus_end.days
        hours_left = int((time_until_bonus_end.total_seconds() % 86400) / 3600)
        minutes_left = int((time_until_bonus_end.total_seconds() % 3600) / 60)
        
        if days_left > 0:
            time_left_text = f"{days_left} день{'а' if 2 <= days_left <= 4 else 'ей'}"
        elif hours_left > 0:
            time_left_text = f"{hours_left} час{'а' if 2 <= hours_left <= 4 else 'ов'}"
        else:
            time_left_text = f"{minutes_left} минут{'ы' if 2 <= minutes_left <= 4 else ''}"
    else:
        time_left_text = "менее минуты"
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_BACK_TO_MENU)]],
        resize_keyboard=True
    )
    
    # Формируем сообщение об отвязке карты
    card_message = ""
    if card_removed:
        card_message = "💳 <b>Карта успешно отвязана и удалена из нашей системы.</b>\n\n"
    else:
        card_message = "ℹ️ <b>Сохраненная карта не найдена.</b>\n\n"
    
    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⏸️ <b>Автопродление отключено</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ Автопродление доступа отключено.\n\n"
        f"{card_message}"
        f"📅 <b>Доступ действует до:</b> {expires_str}\n\n"
        f"⏰ <b>До окончания бонусной недели:</b> {time_left_text}\n\n"
        "⚠️ <b>Важно:</b> После окончания бонусной недели:\n"
        "• Доступ в канал закончится\n"
        "• Вы будете удалены из канала\n"
        "• Для возобновления доступа необходимо оплатить заново\n\n"
        "🔒 <b>Безопасность:</b>\n"
        "• Карта удалена из нашей базы данных\n"
        "• Мы больше не можем использовать вашу карту для автоплатежей",
        parse_mode="HTML",
        reply_markup=keyboard
    )


# Обработчик для кнопки "Отменить доступ и отключить автопродление"
@dp.message(lambda m: (m.text or "").strip() == BTN_CANCEL_SUB)
async def cancel_subscription(message: Message):
    """Обработчик кнопки отмены доступа - отключает автопродление и удаляет способ оплаты"""
    user_id = message.from_user.id
    await send_typing_action(message.chat.id)
    
    # Проверяем, есть ли активная подписка
    expires_at = await get_subscription_expires_at(user_id)
    from datetime import timezone
    now = datetime.now(timezone.utc)
    
    # Убеждаемся, что expires_at имеет timezone для сравнения
    expires_at = ensure_timezone_aware(expires_at)
    
    if not expires_at or expires_at <= now:
        await message.answer(
            "ℹ️ <b>У вас нет активного доступа</b>\n\n"
            "Доступ уже неактивен или отсутствует.",
            parse_mode="HTML",
            reply_markup=await main_menu(user_id)
        )
        return
    
    # Отключаем автопродление И отвязываем карту
    await set_auto_renewal(user_id, False)
    
    # ВАЖНО: Отвязываем карту (удаляем payment_method_id)
    card_removed = await delete_payment_method(user_id)
    
    # Получаем информацию о подписке
    expires_str = format_datetime_moscow(expires_at)
    
    # После отмены доступа показываем меню ТОЛЬКО с кнопкой "О проекте"
    # (без кнопки "Бонус в честь запуска")
    updated_menu = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ABOUT_1)],
        ],
        resize_keyboard=True,
    )
    
    # Формируем сообщение об отвязке карты
    card_message = ""
    if card_removed:
        card_message = "💳 <b>Карта успешно отвязана и удалена из нашей системы.</b>\n\n"
    else:
        card_message = "ℹ️ <b>Сохраненная карта не найдена.</b>\n\n"
    
    await message.answer(
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⏸️ <b>Автопродление отключено</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ Автопродление доступа отключено.\n\n"
        f"{card_message}"
        f"📅 <b>Доступ действует до:</b> {expires_str}\n\n"
        f"💰 <b>Стоимость доступа:</b> {PAYMENT_AMOUNT_RUB} рублей\n\n"
        "💡 После окончания срока доступ не будет продлеваться автоматически.\n\n"
        "🔄 Для возобновления автопродления необходимо оплатить доступ заново.\n\n"
        "🔒 <b>Важно о безопасности:</b>\n"
        "• Карта удалена из нашей базы данных\n"
        "• Мы больше не можем использовать вашу карту для автоплатежей\n"
        "• Если карта видна в личном кабинете YooKassa, вы можете удалить её там вручную\n"
        "• Для этого войдите в личный кабинет YooKassa и удалите сохранённую карту",
        parse_mode="HTML",
        reply_markup=updated_menu  # Показываем обновленное главное меню с кнопкой "Получить доступ"
    )


# Обработчик для кнопки "Возобновить доступ"
@dp.message(lambda m: (m.text or "").strip() == BTN_RESUME_SUB)
async def resume_subscription(message: Message):
    """Обработчик кнопки возобновления доступа - включает автопродление обратно"""
    user_id = message.from_user.id
    await send_typing_action(message.chat.id)
    
    # Проверяем, есть ли активная подписка
    expires_at = await get_subscription_expires_at(user_id)
    now = datetime.now(timezone.utc)
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    
    if not expires_at or expires_at <= now:
        await message.answer(
            "ℹ️ <b>У вас нет активного доступа</b>\n\n"
            "Доступ уже неактивен или отсутствует.",
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
            f"📅 <b>Доступ действует до:</b> {expires_str}\n\n"
            "💡 Для возобновления автопродления необходимо:\n"
            "1️⃣ Оплатить доступ заново\n"
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
            "🔄 Автопродление доступа включено.\n\n"
            f"📅 <b>Доступ действует до:</b> {expires_str}\n"
            f"💳 <b>Следующее списание:</b> {next_payment_str}\n\n"
            f"✅ Доступ будет автоматически продлеваться каждые {format_subscription_duration(SUBSCRIPTION_DAYS)}.",
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
            "Ваш доступ не будет автоматически продлеваться.\n\n"
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
                "1️⃣ Нажмите кнопку 💳 Получить доступ\n"
                "2️⃣ При оплате отметьте галочку «Сохранить карту для следующих платежей»\n"
                "3️⃣ После успешной оплаты нажмите кнопку автопродления ещё раз\n\n"
                f"💡 После этого доступ будет продлеваться автоматически каждые {format_subscription_duration(SUBSCRIPTION_DAYS)}.",
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
                        f"🔄 Доступ будет автоматически продлеваться каждые {format_subscription_duration(SUBSCRIPTION_DAYS)}.\n\n"
                        "💳 Списывание происходит с сохранённой карты автоматически.",
                        parse_mode="HTML",
                        reply_markup=await main_menu(user_id)
                    )
                else:
                    await message.answer(
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "✅ <b>Автопродление включено!</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"🔄 Доступ будет автоматически продлеваться каждые {format_subscription_duration(SUBSCRIPTION_DAYS)} при наличии активного доступа.\n\n"
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
    Автоматически одобряет заявки на вступление только от владельцев ссылок
    """
    if CHANNEL_ID and join_request.chat.id == CHANNEL_ID:
        user_id = join_request.from_user.id
        
        # УПРОЩЕННАЯ ЛОГИКА: проверяем, есть ли у пользователя активная подписка
        # Если есть активная подписка, значит у него есть валидная ссылка
        from db import get_subscription_expires_at
        expires_at = await get_subscription_expires_at(user_id)
        
        if expires_at:
            from datetime import timezone
            now = datetime.now(timezone.utc)
            
            # Убеждаемся, что expires_at имеет timezone для сравнения
            expires_at = ensure_timezone_aware(expires_at)
            
            has_active_subscription = expires_at and expires_at > now
            
            if has_active_subscription:
                # У пользователя есть активная подписка - одобряем заявку
                try:
                    await join_request.approve()
                    print(f"✅ Автоматически одобрена заявка от пользователя {user_id} (активная подписка до {expires_at})")
                except Exception as e:
                    print(f"❌ Ошибка при одобрении заявки от {user_id}: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                # Подписка истекла - отклоняем заявку
                try:
                    await join_request.decline()
                    print(f"🚫 Заявка от пользователя {user_id} отклонена (подписка истекла, expires_at: {expires_at})")
                except Exception as e:
                    print(f"❌ Ошибка при отклонении заявки от {user_id}: {e}")
        else:
            # У пользователя нет подписки - проверяем старый способ (для обратной совместимости)
            if await is_user_allowed(user_id):
                try:
                    await join_request.approve()
                    print(f"✅ Автоматически одобрена заявка от пользователя {user_id} (старый способ проверки)")
                except Exception as e:
                    print(f"❌ Ошибка при одобрении заявки от {user_id}: {e}")
            else:
                try:
                    await join_request.decline()
                    print(f"🚫 Заявка от пользователя {user_id} отклонена (нет ссылки и не оплатил)")
                except Exception as e:
                    print(f"⚠️ Ошибка при отклонении заявки от {user_id}: {e}")


# Обработчик присоединения пользователей к каналу - дополнительная проверка безопасности
@dp.chat_member()
async def on_chat_member_update(update: ChatMemberUpdated):
    """Дополнительная проверка безопасности при присоединении пользователей к каналу"""
    # Проверяем, что это наш канал
    if update.chat.id != CHANNEL_ID:
        return
    
    # Проверяем, что пользователь присоединился (стал member)
    if update.new_chat_member.status == ChatMemberStatus.MEMBER:
        user_id = update.new_chat_member.user.id
        
        # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: проверяем, что у пользователя есть активная подписка
        # Это защита на случай, если кто-то каким-то образом обошел проверку заявки
        from db import get_subscription_expires_at
        expires_at = await get_subscription_expires_at(user_id)
        from datetime import datetime
        now = datetime.now(timezone.utc)
        has_active_subscription = expires_at and expires_at > now
        
        if not has_active_subscription:
            # У пользователя нет активной подписки - баним его
            try:
                await bot.ban_chat_member(
                    chat_id=CHANNEL_ID,
                    user_id=user_id,
                    until_date=None  # Бан навсегда
                )
                print(f"🚫 Пользователь {user_id} забанен - нет активной подписки (дополнительная проверка безопасности)")
            except Exception as e:
                print(f"⚠️ Ошибка при бане пользователя {user_id}: {e}")
        else:
            print(f"✅ Пользователь {user_id} присоединился к каналу - проверка пройдена (есть активная подписка)")


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

