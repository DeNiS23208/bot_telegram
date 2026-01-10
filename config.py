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
# Фиксированные даты бонусной недели
# КРИТИЧЕСКИ ВАЖНО: Дата окончания должна быть одинаковой для ВСЕХ пользователей
# Правильная дата окончания: 14.01.2026 10:58:42 UTC (13:58:42 МСК)
BONUS_WEEK_END_DATE = datetime(2026, 1, 14, 10, 58, 42, tzinfo=timezone.utc)  # Конец бонусной недели
# Начало вычисляется от даты окончания (для новых пользователей, если в БД нет времени)
BONUS_WEEK_START_DATE = BONUS_WEEK_END_DATE - timedelta(days=7)  # Начало: 07.01.2026 10:58:42 UTC

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
# КРИТИЧЕСКИ ВАЖНО: Используем время, которое УЖЕ есть в БД (если есть)
# НЕ изменяем существующее время в БД - это сохранит правильное время для уже зарегистрированных пользователей
# Если в БД нет времени - используем вычисленное время от даты окончания (только для первого запуска)
_BONUS_WEEK_START = _get_bonus_week_start_from_db()
if _BONUS_WEEK_START is None:
    # Если в БД нет времени - устанавливаем вычисленное время от даты окончания
    # Это гарантирует, что новые пользователи получат правильное время
    _BONUS_WEEK_START = BONUS_WEEK_START_DATE
    _set_bonus_week_start_to_db(_BONUS_WEEK_START)
# Если в БД УЖЕ есть время - НЕ меняем его! Используем существующее для всех пользователей

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
    # КРИТИЧЕСКИ ВАЖНО: Используем фиксированную дату окончания из get_bonus_week_end()
    # Это гарантирует, что ВСЕ пользователи имеют одинаковую дату окончания (14.01.2026 10:58:42 UTC)
    # Время начала может быть разным в БД, но окончание всегда одинаковое
    now = datetime.now(timezone.utc)
    bonus_end = get_bonus_week_end()
    
    # Получаем время начала (из БД или вычисленное)
    bonus_start = get_bonus_week_start()
    
    # Проверяем, активна ли бонусная неделя: от начала до фиксированного окончания
    return bonus_start <= now < bonus_end

def get_bonus_week_start() -> datetime:
    """Возвращает время начала бонусной недели (читается из БД)"""
    global _BONUS_WEEK_START
    # Если время не установлено - читаем из БД
    if _BONUS_WEEK_START is None:
        _BONUS_WEEK_START = _get_bonus_week_start_from_db()
        if _BONUS_WEEK_START is None:
            # КРИТИЧЕСКИ ВАЖНО: Используем фиксированное время начала из BONUS_WEEK_START_DATE
            # Это гарантирует, что ВСЕ пользователи имеют одинаковое время начала бонусной недели
            _BONUS_WEEK_START = BONUS_WEEK_START_DATE
            _set_bonus_week_start_to_db(_BONUS_WEEK_START)
    return _BONUS_WEEK_START

def get_bonus_week_end() -> datetime:
    """Возвращает время окончания бонусной недели"""
    # КРИТИЧЕСКИ ВАЖНО: Фиксированная дата окончания бонусной недели
    # Правильная дата окончания: 14.01.2026 10:58:42 UTC (13:58:42 МСК)
    # Эта дата должна быть одинаковой для ВСЕХ пользователей
    return datetime(2026, 1, 14, 10, 58, 42, tzinfo=timezone.utc)

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

