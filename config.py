"""
Конфигурация и константы бота
"""
from datetime import datetime, timedelta, timezone

# Временные интервалы
PAYMENT_LINK_VALID_MINUTES = 10  # Срок действия ссылки на оплату
# Длительность подписки
SUBSCRIPTION_DAYS = 30  # Длительность подписки (30 дней для продакшн режима)
SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS = 3  # За сколько дней уведомлять об истечении
SUBSCRIPTION_EXPIRING_NOTIFICATION_WINDOW_HOURS = 24  # Окно для уведомления (часы)

# Интервалы проверки фоновых задач
CHECK_EXPIRED_PAYMENTS_INTERVAL_SECONDS = 60  # Проверка истекших платежей (секунды) - проверка каждую минуту для точного уведомления через 10 минут
CHECK_EXPIRED_SUBSCRIPTIONS_INTERVAL_SECONDS = 120  # Проверка истекших подписок (секунды) - увеличено для снижения нагрузки
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

# Переменные для тестирования (в минутах)
dni_prazdnika = 60  # Длительность бонусной недели в минутах (1 час)
vremya_sms = 20  # Время уведомления до окончания в минутах (20 минут)

# Фиксированное время начала бонусной недели для теста (устанавливается при первом импорте)
_BONUS_WEEK_TEST_START = None

# Для продакшена (после теста):
# dni_prazdnika = 7 * 24 * 60  # 7 дней в минутах
# vremya_sms = 2 * 60  # 2 часа в минутах

# Цены и длительность бонусной недели
BONUS_WEEK_PRICE_RUB = "1.00"  # Цена бонусной недели
BONUS_WEEK_DURATION_MINUTES = dni_prazdnika  # Длительность в минутах

# Продакшн значения (после окончания бонусной недели)
PRODUCTION_PRICE_RUB = "2990.00"  # Обычная цена подписки
PRODUCTION_DURATION_DAYS = 30  # Обычная длительность подписки

# ================== ФУНКЦИИ ДЛЯ ОПРЕДЕЛЕНИЯ РЕЖИМА ==================
def is_bonus_week_active() -> bool:
    """Проверяет, активна ли сейчас бонусная неделя"""
    # Для теста: используем фиксированное время начала (устанавливается при первом вызове)
    # Для продакшена: используем фиксированные даты
    USE_TEST_MODE = True  # True = тестовый режим (бонусная неделя начинается при первом обращении)
    
    if USE_TEST_MODE:
        # Тестовый режим: бонусная неделя начинается с момента первого вызова и длится dni_prazdnika минут
        global _BONUS_WEEK_TEST_START
        now = datetime.now(timezone.utc)
        
        # Если начало еще не установлено, устанавливаем его сейчас
        if _BONUS_WEEK_TEST_START is None:
            _BONUS_WEEK_TEST_START = now
        
        test_end = _BONUS_WEEK_TEST_START + timedelta(minutes=dni_prazdnika)
        return _BONUS_WEEK_TEST_START <= now < test_end
    else:
        # Продакшн режим: используем фиксированные даты
        now = datetime.now(timezone.utc)
        return BONUS_WEEK_START_DATE <= now <= BONUS_WEEK_END_DATE

def get_bonus_week_start() -> datetime:
    """Возвращает время начала бонусной недели"""
    USE_TEST_MODE = True  # True = тестовый режим
    if USE_TEST_MODE:
        global _BONUS_WEEK_TEST_START
        if _BONUS_WEEK_TEST_START is None:
            _BONUS_WEEK_TEST_START = datetime.now(timezone.utc)
        return _BONUS_WEEK_TEST_START
    else:
        return BONUS_WEEK_START_DATE

def get_bonus_week_end() -> datetime:
    """Возвращает время окончания бонусной недели"""
    USE_TEST_MODE = True  # True = тестовый режим
    if USE_TEST_MODE:
        return get_bonus_week_start() + timedelta(minutes=dni_prazdnika)
    else:
        return BONUS_WEEK_END_DATE

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

