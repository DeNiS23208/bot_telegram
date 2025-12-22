import os
import uuid
from typing import Optional

from dotenv import load_dotenv
from yookassa import Configuration, Payment

load_dotenv()

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
):
    """
    Создаёт платёж и возвращает (payment_id, confirmation_url)

    ВАЖНО:
    - customer_email нужен для чека (54-ФЗ)
    - telegram_user_id кладём в metadata, чтобы webhook знал кому отправить инвайт
    - payment_subject/payment_mode обязательны, иначе BadRequestError
    """
    idempotence_key = str(uuid.uuid4())

    payload = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": description,
        
        # Сохраняем метод оплаты для возможности автопродления
        "save_payment_method": True,

        # ✅ КРИТИЧНО: это нужно webhook'у
        "metadata": {"telegram_user_id": str(telegram_user_id)},

        "receipt": {
            "customer": {"email": customer_email},
            "items": [
                {
                    "description": "Доступ в закрытый Telegram-канал (30 дней)",
                    "quantity": "1.00",
                    "amount": {"value": amount_rub, "currency": "RUB"},
                    "vat_code": 1,
                    "payment_subject": "service",
                    "payment_mode": "full_payment",
                }
            ],
        },
    }

    payment = Payment.create(payload, idempotence_key)
    return payment.id, payment.confirmation.confirmation_url


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

