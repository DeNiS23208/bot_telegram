"""
Конфигурация и константы бота
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "bot.db")

# Временные интервалы
PAYMENT_LINK_VALID_MINUTES = 10  # Срок действия ссылки на оплату
# Длительность подписки
SUBSCRIPTION_DAYS = 30  # Длительность подписки (30 дней для продакшн режима)
# Уведомление за 2 часа до истечения (2 часа = 2/24 дней)
SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS = 2 / 24  # За сколько дней уведомлять об истечении (2 часа)
SUBSCRIPTION_EXPIRING_NOTIFICATION_WINDOW_HOURS = 24  # Окно для уведомления (часы) - период, в течение которого уведомление может быть отправлено

# Интервалы проверки фоновых задач
CHECK_EXPIRED_PAYMENTS_INTERVAL_SECONDS = 60  # Проверка истекших платежей (секунды) - проверка каждую минуту для точного уведомления через 10 минут
CHECK_EXPIRED_SUBSCRIPTIONS_INTERVAL_SECONDS = 10  # Проверка истекших подписок (секунды) - уменьшено для точного срабатывания
CHECK_EXPIRING_SUBSCRIPTIONS_INTERVAL_SECONDS = 3600  # Проверка истекающих подписок (секунды)

# Ограничения для уведомлений
MAX_NOTIFIED_USERS_CACHE_SIZE = 100  # Максимальный размер кэша уведомленных пользователей

# Размеры файлов для видео
MAX_VIDEO_SIZE_MB = 50  # Максимальный размер видео для отправки (MB)
MAX_ANIMATION_SIZE_MB = 20  # Максимальный размер для отправки как animation (MB)
MAX_ANIMATION_DURATION_SECONDS = 20  # Максимальная длительность для отправки как animation (секунды)

# Сумма платежа
PAYMENT_AMOUNT_RUB = "2990.00"  # Сумма платежа в рублях (продакшн режим)

# ================== БОНУСНАЯ НЕДЕЛЯ ==================
# Для продакшена: с 5 января по 12 января 2025
# Для теста: используем переменные для быстрой проверки
BONUS_WEEK_START_DATE = datetime(2025, 1, 5, 0, 0, 0, tzinfo=timezone.utc)  # Начало бонусной недели (5 января)
BONUS_WEEK_END_DATE = datetime(2025, 1, 12, 23, 59, 59, tzinfo=timezone.utc)  # Конец бонусной недели (12 января)

# Переменные для продакшн режима (в минутах)
dni_prazdnika = 7 * 24 * 60  # Длительность бонусной недели в минутах (7 дней = 10080 минут)
vremya_sms = 2 * 60  # Время уведомления до окончания в минутах (2 часа = 120 минут)

# КРИТИЧЕСКИ ВАЖНО: Время начала бонусной недели читается из базы данных
# Если в БД нет времени - устанавливается новое и сохраняется в БД
# Это гарантирует, что при перезапуске сервисов время начала НЕ сбрасывается

def _get_bonus_week_start_from_db() -> Optional[datetime]:
    """Читает время начала бонусной недели из базы данных (синхронно)"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT start_time FROM bonus_week_config WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0]:
            start_time = datetime.fromisoformat(row[0])
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            return start_time
    except Exception as e:
        # Если таблицы еще нет или ошибка - вернем None
        pass
    return None

def _set_bonus_week_start_to_db(start_time: datetime) -> None:
    """Сохраняет время начала бонусной недели в базу данных (синхронно)"""
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    
    start_time_str = start_time.isoformat()
    updated_at_str = datetime.now(timezone.utc).isoformat()
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Создаем таблицу если её нет
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bonus_week_config (
                id INTEGER PRIMARY KEY DEFAULT 1,
                start_time TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (id = 1)
            )
        """)
        # Сохраняем время
        cursor.execute("""
            INSERT OR REPLACE INTO bonus_week_config (id, start_time, updated_at)
            VALUES (1, ?, ?)
        """, (start_time_str, updated_at_str))
        conn.commit()
        conn.close()
    except Exception as e:
        # Игнорируем ошибки при сохранении
        pass

# Инициализация времени начала бонусной недели
_BONUS_WEEK_START = _get_bonus_week_start_from_db()
if _BONUS_WEEK_START is None:
    # Если в БД нет времени - устанавливаем новое и сохраняем
    _BONUS_WEEK_START = datetime.now(timezone.utc)
    _set_bonus_week_start_to_db(_BONUS_WEEK_START)

def reset_bonus_week():
    """Сбрасывает начало бонусной недели (для тестирования)"""
    global _BONUS_WEEK_START
    _BONUS_WEEK_START = datetime.now(timezone.utc)
    _set_bonus_week_start_to_db(_BONUS_WEEK_START)

# Цены и длительность бонусной недели
BONUS_WEEK_PRICE_RUB = "1.00"  # Цена бонусной недели
BONUS_WEEK_DURATION_MINUTES = dni_prazdnika  # Длительность в минутах

# Продакшн значения (после окончания бонусной недели)
PRODUCTION_PRICE_RUB = "2990.00"  # Обычная цена подписки (2990 рублей)
PRODUCTION_DURATION_DAYS = 30.0  # Обычная длительность подписки (30 дней)

# Автопродление
AUTO_RENEWAL_ATTEMPT_INTERVAL_MINUTES = 120  # Интервал между попытками автопродления (2 часа = 120 минут)

# ================== ФУНКЦИИ ДЛЯ ОПРЕДЕЛЕНИЯ РЕЖИМА ==================
def is_bonus_week_active() -> bool:
    """Проверяет, активна ли сейчас бонусная неделя"""
    # Время начала читается из базы данных, чтобы не сбрасывалось при перезапуске
    global _BONUS_WEEK_START
    now = datetime.now(timezone.utc)
    
    # Если время не установлено - читаем из БД
    if _BONUS_WEEK_START is None:
        _BONUS_WEEK_START = _get_bonus_week_start_from_db()
        if _BONUS_WEEK_START is None:
            # Если в БД тоже нет - устанавливаем новое
            _BONUS_WEEK_START = datetime.now(timezone.utc)
            _set_bonus_week_start_to_db(_BONUS_WEEK_START)
    
    bonus_end = _BONUS_WEEK_START + timedelta(minutes=dni_prazdnika)
    return _BONUS_WEEK_START <= now < bonus_end

def get_bonus_week_start() -> datetime:
    """Возвращает время начала бонусной недели (читается из БД)"""
    global _BONUS_WEEK_START
    # Если время не установлено - читаем из БД
    if _BONUS_WEEK_START is None:
        _BONUS_WEEK_START = _get_bonus_week_start_from_db()
        if _BONUS_WEEK_START is None:
            # Если в БД тоже нет - устанавливаем новое
            _BONUS_WEEK_START = datetime.now(timezone.utc)
            _set_bonus_week_start_to_db(_BONUS_WEEK_START)
    return _BONUS_WEEK_START

def get_bonus_week_end() -> datetime:
    """Возвращает время окончания бонусной недели"""
    return get_bonus_week_start() + timedelta(minutes=dni_prazdnika)

def get_current_subscription_price() -> str:
    """Возвращает текущую цену подписки в зависимости от режима"""
    return BONUS_WEEK_PRICE_RUB if is_bonus_week_active() else PRODUCTION_PRICE_RUB

def get_current_subscription_duration() -> float:
    """Возвращает текущую длительность подписки в зависимости от режима"""
    if is_bonus_week_active():
        # Бонусная неделя: возвращаем ОСТАВШЕЕСЯ время до конца бонусной недели в днях
        now = datetime.now(timezone.utc)
        bonus_end = get_bonus_week_end()
        remaining_time = bonus_end - now
        if remaining_time.total_seconds() <= 0:
            # Бонусная неделя уже закончилась
            return PRODUCTION_DURATION_DAYS
        # Конвертируем секунды в дни
        remaining_days = remaining_time.total_seconds() / 86400
        return remaining_days
    else:
        # Продакшн: 30 дней
        return PRODUCTION_DURATION_DAYS

def get_production_subscription_price() -> str:
    """Возвращает цену продакшн подписки (для автопродления после бонусной недели)"""
    return PRODUCTION_PRICE_RUB

def get_production_subscription_duration() -> float:
    """Возвращает длительность продакшн подписки (для автопродления после бонусной недели)"""
    return PRODUCTION_DURATION_DAYS

