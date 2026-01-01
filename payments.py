import os
import uuid
from typing import Optional

from dotenv import load_dotenv
from yookassa import Configuration, Payment, Refund
from config import SUBSCRIPTION_DAYS

load_dotenv()

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

# Настройка ЮKassa из .env
Configuration.account_id = os.getenv("YOOKASSA_SHOP_ID")
Configuration.secret_key = os.getenv("YOOKASSA_SECRET_KEY")

if not Configuration.account_id or not Configuration.secret_key:
    raise RuntimeError("YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY is missing in .env")


def create_payment(
    amount_rub: str,
    description: str,
    return_url: str,
    customer_email: str,
    telegram_user_id: int,
    enable_save_payment_method: bool = False,
):
    """
    Создаёт платёж и возвращает (payment_id, confirmation_url)

    ВАЖНО:
    - customer_email нужен для чека (54-ФЗ)
    - telegram_user_id кладём в metadata, чтобы webhook знал кому отправить инвайт
    - payment_subject/payment_mode обязательны, иначе BadRequestError
    - enable_save_payment_method: если True, пытается включить сохранение способа оплаты
      (работает только если магазин настроен для автоплатежей в ЮKassa)
    """
    idempotence_key = str(uuid.uuid4())

    payload = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": description,

        # ✅ КРИТИЧНО: это нужно webhook'у
        "metadata": {"telegram_user_id": str(telegram_user_id)},

        # ✅ ВАЖНО: merchant_customer_id нужен для сохранения способа оплаты
        "merchant_customer_id": str(telegram_user_id),

        "receipt": {
            "customer": {"email": customer_email},
            "items": [
                {
                    "description": f"Доступ в закрытый Telegram-канал ({format_subscription_duration(SUBSCRIPTION_DAYS)})",
                    "quantity": "1.00",
                    "amount": {"value": amount_rub, "currency": "RUB"},
                    "vat_code": 1,
                    "payment_subject": "service",
                    "payment_mode": "full_payment",
                }
            ],
        },
    }
    
    # Пытаемся включить условное сохранение способа оплаты, если запрошено
    # ВАЖНО: это работает только если магазин настроен для автоплатежей в ЮKassa
    # Если магазин не настроен, этот параметр вызовет ошибку ForbiddenError
    # save_payment_method: true - это условное сохранение (пользователь может выбрать на форме оплаты)
    if enable_save_payment_method:
        payload["save_payment_method"] = True  # Условное сохранение - пользователь выбирает на форме оплаты

    try:
        payment = Payment.create(payload, idempotence_key)
        return payment.id, payment.confirmation.confirmation_url
    except Exception as e:
        # Если ошибка связана с save_payment_method, пробуем без него
        if enable_save_payment_method and ("recurring" in str(e).lower() or "forbidden" in str(e).lower()):
            print(f"⚠️ Магазин не настроен для автоплатежей, создаю платеж без save_payment_method: {e}")
            payload.pop("save_payment_method", None)
            payment = Payment.create(payload, idempotence_key)
            return payment.id, payment.confirmation.confirmation_url
        raise


def get_payment_status(payment_id: str) -> str:
    payment = Payment.find_one(payment_id)
    return payment.status


def get_payment_url(payment_id: str) -> Optional[str]:
    """Получает URL для оплаты по payment_id"""
    try:
        payment = Payment.find_one(payment_id)
        if payment.confirmation and payment.confirmation.confirmation_url:
            return payment.confirmation.confirmation_url
        return None
    except Exception:
        return None


def create_auto_payment(
    amount_rub: str,
    description: str,
    customer_email: str,
    telegram_user_id: int,
    payment_method_id: str,
) -> tuple[str, str]:
    """
    Создает автоматический платеж с использованием сохраненного способа оплаты
    Возвращает (payment_id, status)
    
    ВАЖНО: Эта функция используется для автопродления подписки
    """
    import uuid
    idempotence_key = str(uuid.uuid4())
    
    payload = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        "capture": True,
        "description": description,
        "payment_method_id": payment_method_id,  # Используем сохраненный способ оплаты
        
        # ✅ КРИТИЧНО: это нужно webhook'у
        "metadata": {"telegram_user_id": str(telegram_user_id), "auto_renewal": "true"},
        
        # ✅ ВАЖНО: merchant_customer_id нужен для автоплатежей
        "merchant_customer_id": str(telegram_user_id),
        
        "receipt": {
            "customer": {"email": customer_email},
            "items": [
                {
                    "description": f"Автопродление подписки на закрытый Telegram-канал ({format_subscription_duration(SUBSCRIPTION_DAYS)})",
                    "quantity": "1.00",
                    "amount": {"value": amount_rub, "currency": "RUB"},
                    "vat_code": 1,
                    "payment_subject": "service",
                    "payment_mode": "full_payment",
                }
            ],
        },
    }
    
    try:
        payment = Payment.create(payload, idempotence_key)
        # Логируем детали для отладки
        payment_method_type = None
        try:
            if hasattr(payment, 'payment_method') and payment.payment_method:
                pm = payment.payment_method
                if hasattr(pm, 'type'):
                    payment_method_type = pm.type
                elif isinstance(pm, dict) and 'type' in pm:
                    payment_method_type = pm['type']
        except Exception:
            pass
        print(f"🔍 Создан автоплатеж: payment_id={payment.id}, status={payment.status}, payment_method_id={payment_method_id}, тип: {payment_method_type}")
        if hasattr(payment, 'cancellation_details') and payment.cancellation_details:
            cd = payment.cancellation_details
            party = getattr(cd, 'party', None) if hasattr(cd, 'party') else None
            reason = getattr(cd, 'reason', None) if hasattr(cd, 'reason') else None
            print(f"⚠️ Детали отмены автоплатежа: party={party}, reason={reason}")
            print(f"⚠️ Полный объект cancellation_details: {cd}")
        return payment.id, payment.status
    except Exception as e:
        # Логируем ошибку для отладки
        print(f"❌ Ошибка создания автоматического платежа: {e}")
        import traceback
        traceback.print_exc()
        raise


def create_refund(
    payment_id: str,
    amount_rub: Optional[str] = None,
    description: Optional[str] = None,
) -> tuple[str, str]:
    """
    Создает возврат средств для платежа
    
    Args:
        payment_id: ID платежа, для которого создается возврат
        amount_rub: Сумма возврата (если None, возвращается полная сумма платежа)
        description: Описание возврата (опционально)
    
    Returns:
        tuple[str, str]: (refund_id, status) - ID возврата и его статус
    
    ВАЖНО: 
    - Возвраты работают для всех типов платежей (SberPay, СБП, банковская карта)
    - Если amount_rub не указан, возвращается полная сумма платежа
    - Возврат можно создать только для успешных платежей (status=succeeded)
    """
    import uuid
    idempotence_key = str(uuid.uuid4())
    
    # Получаем информацию о платеже
    try:
        payment = Payment.find_one(payment_id)
    except Exception as e:
        raise ValueError(f"Не удалось найти платеж {payment_id}: {e}")
    
    # Проверяем, что платеж успешен
    if payment.status != "succeeded":
        raise ValueError(f"Нельзя создать возврат для платежа со статусом {payment.status}. Требуется статус 'succeeded'")
    
    # Получаем сумму платежа, если amount_rub не указан
    if amount_rub is None:
        if hasattr(payment.amount, 'value'):
            amount_rub = payment.amount.value
        elif isinstance(payment.amount, dict):
            amount_rub = payment.amount.get('value')
        else:
            raise ValueError("Не удалось определить сумму платежа")
    
    # Формируем payload для возврата
    payload = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        "payment_id": payment_id,
    }
    
    if description:
        payload["description"] = description
    
    try:
        refund = Refund.create(payload, idempotence_key)
        print(f"✅ Создан возврат: refund_id={refund.id}, payment_id={payment_id}, amount={amount_rub}, status={refund.status}")
        return refund.id, refund.status
    except Exception as e:
        print(f"❌ Ошибка создания возврата для платежа {payment_id}: {e}")
        import traceback
        traceback.print_exc()
        raise


def get_refund_status(refund_id: str) -> str:
    """Получает статус возврата по его ID"""
    try:
        refund = Refund.find_one(refund_id)
        return refund.status
    except Exception as e:
        raise ValueError(f"Не удалось найти возврат {refund_id}: {e}")

