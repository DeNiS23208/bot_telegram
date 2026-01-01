"""
Утилиты для безопасной работы с Telegram API
Включает retry логику, обработку таймаутов и rate limiting
"""
import asyncio
import logging
from typing import Optional, Callable, Any
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramAPIError

logger = logging.getLogger(__name__)

# Константы для retry
MAX_RETRIES = 3
RETRY_DELAY = 1  # секунды
TIMEOUT_LONG = 60  # для больших файлов
TIMEOUT_SHORT = 30  # для обычных запросов


async def safe_send_message(
    bot: Bot,
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Any] = None,
    max_retries: int = MAX_RETRIES,
    timeout: int = TIMEOUT_SHORT
) -> Optional[Any]:
    """
    Безопасная отправка сообщения с retry логикой
    """
    for attempt in range(max_retries):
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                request_timeout=timeout
            )
        except TelegramRetryAfter as e:
            # Telegram просит подождать
            wait_time = e.retry_after
            logger.warning(f"⚠️ Rate limit для chat_id={chat_id}, ждем {wait_time} секунд")
            await asyncio.sleep(wait_time)
            continue
        except TelegramNetworkError as e:
            # Проблемы с сетью
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(f"⚠️ Сетевая ошибка при отправке сообщения (попытка {attempt + 1}/{max_retries}): {e}, ждем {wait_time}с")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"❌ Не удалось отправить сообщение после {max_retries} попыток: {e}")
                return None
        except TelegramAPIError as e:
            # Ошибка API (не retryable)
            logger.error(f"❌ Ошибка Telegram API при отправке сообщения: {e}")
            return None
        except Exception as e:
            # Другие ошибки
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(f"⚠️ Неожиданная ошибка при отправке сообщения (попытка {attempt + 1}/{max_retries}): {e}, ждем {wait_time}с")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"❌ Критическая ошибка при отправке сообщения: {e}")
                return None
    
    return None


async def safe_send_video(
    bot: Bot,
    chat_id: int,
    video: Any,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[Any] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    duration: Optional[int] = None,
    max_retries: int = MAX_RETRIES,
    timeout: int = TIMEOUT_LONG
) -> Optional[Any]:
    """
    Безопасная отправка видео с retry логикой и увеличенным таймаутом
    """
    for attempt in range(max_retries):
        try:
            params = {
                "chat_id": chat_id,
                "video": video,
                "request_timeout": timeout
            }
            if caption:
                params["caption"] = caption
            if parse_mode:
                params["parse_mode"] = parse_mode
            if reply_markup:
                params["reply_markup"] = reply_markup
            if width:
                params["width"] = width
            if height:
                params["height"] = height
            if duration:
                params["duration"] = duration
            
            return await bot.send_video(**params)
        except TelegramRetryAfter as e:
            wait_time = e.retry_after
            logger.warning(f"⚠️ Rate limit для видео chat_id={chat_id}, ждем {wait_time} секунд")
            await asyncio.sleep(wait_time)
            continue
        except TelegramNetworkError as e:
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1) * 2  # Больше времени для видео
                logger.warning(f"⚠️ Сетевая ошибка при отправке видео (попытка {attempt + 1}/{max_retries}): {e}, ждем {wait_time}с")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"❌ Не удалось отправить видео после {max_retries} попыток: {e}")
                return None
        except TelegramAPIError as e:
            logger.error(f"❌ Ошибка Telegram API при отправке видео: {e}")
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1) * 2
                logger.warning(f"⚠️ Неожиданная ошибка при отправке видео (попытка {attempt + 1}/{max_retries}): {e}, ждем {wait_time}с")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"❌ Критическая ошибка при отправке видео: {e}")
                return None
    
    return None


async def safe_create_invite_link(
    bot: Bot,
    chat_id: int,
    creates_join_request: bool = False,
    member_limit: Optional[int] = None,
    expire_date: Optional[Any] = None,
    max_retries: int = MAX_RETRIES,
    timeout: int = TIMEOUT_SHORT
) -> Optional[str]:
    """
    Безопасное создание invite link с retry логикой
    """
    for attempt in range(max_retries):
        try:
            params = {
                "chat_id": chat_id,
                "request_timeout": timeout
            }
            if creates_join_request is not None:
                params["creates_join_request"] = creates_join_request
            if member_limit is not None:
                params["member_limit"] = member_limit
            if expire_date is not None:
                params["expire_date"] = expire_date
            
            invite = await bot.create_chat_invite_link(**params)
            return invite.invite_link
        except TelegramRetryAfter as e:
            wait_time = e.retry_after
            logger.warning(f"⚠️ Rate limit при создании ссылки, ждем {wait_time} секунд")
            await asyncio.sleep(wait_time)
            continue
        except TelegramNetworkError as e:
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(f"⚠️ Сетевая ошибка при создании ссылки (попытка {attempt + 1}/{max_retries}): {e}, ждем {wait_time}с")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"❌ Не удалось создать ссылку после {max_retries} попыток: {e}")
                return None
        except TelegramAPIError as e:
            # Если ошибка из-за несовместимых параметров, не retry
            if "member limit" in str(e).lower() or "bad request" in str(e).lower():
                logger.warning(f"⚠️ Ошибка параметров при создании ссылки: {e}")
                return None
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(f"⚠️ Ошибка API при создании ссылки (попытка {attempt + 1}/{max_retries}): {e}, ждем {wait_time}с")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"❌ Ошибка Telegram API при создании ссылки: {e}")
                return None
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(f"⚠️ Неожиданная ошибка при создании ссылки (попытка {attempt + 1}/{max_retries}): {e}, ждем {wait_time}с")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"❌ Критическая ошибка при создании ссылки: {e}")
                return None
    
    return None


async def safe_bot_action(
    bot: Bot,
    action: Callable,
    *args,
    max_retries: int = MAX_RETRIES,
    timeout: int = TIMEOUT_SHORT,
    **kwargs
) -> Optional[Any]:
    """
    Универсальная функция для безопасного выполнения действий бота
    """
    for attempt in range(max_retries):
        try:
            if "request_timeout" not in kwargs:
                kwargs["request_timeout"] = timeout
            return await action(*args, **kwargs)
        except TelegramRetryAfter as e:
            wait_time = e.retry_after
            logger.warning(f"⚠️ Rate limit, ждем {wait_time} секунд")
            await asyncio.sleep(wait_time)
            continue
        except TelegramNetworkError as e:
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(f"⚠️ Сетевая ошибка (попытка {attempt + 1}/{max_retries}): {e}, ждем {wait_time}с")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"❌ Не удалось выполнить действие после {max_retries} попыток: {e}")
                return None
        except TelegramAPIError as e:
            logger.error(f"❌ Ошибка Telegram API: {e}")
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(f"⚠️ Неожиданная ошибка (попытка {attempt + 1}/{max_retries}): {e}, ждем {wait_time}с")
                await asyncio.sleep(wait_time)
                continue
            else:
                logger.error(f"❌ Критическая ошибка: {e}")
                return None
    
    return None

