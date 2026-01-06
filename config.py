"""
Конфигурация и константы бота
"""
from datetime import datetime, timedelta, timezone

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

# Фиксированное время начала бонусной недели для теста (устанавливается при импорте модуля)
# КРИТИЧЕСКИ ВАЖНО: Устанавливаем при импорте, чтобы бонусная неделя начиналась с момента запуска сервиса
_BONUS_WEEK_TEST_START = datetime.now(timezone.utc)

def reset_bonus_week():
    """Сбрасывает начало бонусной недели (для тестирования)"""
    global _BONUS_WEEK_TEST_START
    _BONUS_WEEK_TEST_START = datetime.now(timezone.utc)

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
    # Продакшн режим: бонусная неделя начинается динамически при первом обращении и длится 7 дней
    global _BONUS_WEEK_TEST_START
    now = datetime.now(timezone.utc)
    
    # _BONUS_WEEK_TEST_START устанавливается при импорте модуля, но на всякий случай проверяем
    if _BONUS_WEEK_TEST_START is None:
        _BONUS_WEEK_TEST_START = now
    
    bonus_end = _BONUS_WEEK_TEST_START + timedelta(minutes=dni_prazdnika)
    return _BONUS_WEEK_TEST_START <= now < bonus_end

def get_bonus_week_start() -> datetime:
    """Возвращает время начала бонусной недели"""
    global _BONUS_WEEK_TEST_START
    # _BONUS_WEEK_TEST_START устанавливается при импорте модуля, но на всякий случай проверяем
    if _BONUS_WEEK_TEST_START is None:
        _BONUS_WEEK_TEST_START = datetime.now(timezone.utc)
    return _BONUS_WEEK_TEST_START

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

