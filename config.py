"""
Конфигурация и константы бота
"""
from datetime import timedelta

# Временные интервалы
PAYMENT_LINK_VALID_MINUTES = 10  # Срок действия ссылки на оплату
# ВРЕМЕННО ДЛЯ ТЕСТИРОВАНИЯ: 5 минут вместо 30 дней
SUBSCRIPTION_DAYS = 5 / 1440  # Длительность подписки (5 минут = 5/1440 дней ≈ 0.00347)
SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS = 3  # За сколько дней уведомлять об истечении
SUBSCRIPTION_EXPIRING_NOTIFICATION_WINDOW_HOURS = 24  # Окно для уведомления (часы)

# Интервалы проверки фоновых задач
CHECK_EXPIRED_PAYMENTS_INTERVAL_SECONDS = 60  # Проверка истекших платежей (секунды)
# ВРЕМЕННО ДЛЯ ТЕСТИРОВАНИЯ: проверка каждые 30 секунд вместо 3600
CHECK_EXPIRED_SUBSCRIPTIONS_INTERVAL_SECONDS = 30  # Проверка истекших подписок (секунды)
CHECK_EXPIRING_SUBSCRIPTIONS_INTERVAL_SECONDS = 3600  # Проверка истекающих подписок (секунды)

# Ограничения для уведомлений
MAX_NOTIFIED_USERS_CACHE_SIZE = 100  # Максимальный размер кэша уведомленных пользователей

# Размеры файлов для видео
MAX_VIDEO_SIZE_MB = 50  # Максимальный размер видео для отправки (MB)
MAX_ANIMATION_SIZE_MB = 20  # Максимальный размер для отправки как animation (MB)
MAX_ANIMATION_DURATION_SECONDS = 20  # Максимальная длительность для отправки как animation (секунды)

# Сумма платежа
PAYMENT_AMOUNT_RUB = "1.00"  # Сумма платежа в рублях

