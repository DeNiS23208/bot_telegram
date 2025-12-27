"""
Конфигурация и константы бота
"""
from datetime import timedelta

# Временные интервалы
PAYMENT_LINK_VALID_MINUTES = 10  # Срок действия ссылки на оплату
# Длительность подписки
SUBSCRIPTION_DAYS = 1  # Длительность подписки (1 день)
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
PAYMENT_AMOUNT_RUB = "1.00"  # Сумма платежа в рублях

