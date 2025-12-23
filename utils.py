"""
Утилиты для бота
"""
import pytz
from datetime import datetime

MoscowTz = pytz.timezone('Europe/Moscow')


def format_datetime_moscow(dt: datetime) -> str:
    """
    Форматирует datetime в строку МСК времени в формате: "число месяц год и время по МСК"
    Пример: "21 декабря 2025 и 19:45 по МСК"
    """
    # Преобразуем UTC в МСК
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    moscow_dt = dt.astimezone(MoscowTz)
    
    # Месяцы на русском
    months = [
        'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
        'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
    ]
    
    day = moscow_dt.day
    month = months[moscow_dt.month - 1]
    year = moscow_dt.year
    time_str = moscow_dt.strftime('%H:%M')
    
    return f"{day} {month} {year} и {time_str} по МСК"

