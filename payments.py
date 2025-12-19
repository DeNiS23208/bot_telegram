import os
import uuid

from dotenv import load_dotenv
from yookassa import Configuration, Payment

load_dotenv()

Configuration.account_id = os.getenv("YOOKASSA_SHOP_ID")
Configuration.secret_key = os.getenv("YOOKASSA_SECRET_KEY")


def create_payment(
    amount_rub: str,
    description: str,
    return_url: str,
    customer_email: str,
    telegram_user_id: int | None = None,
):
    """
    Создаёт платёж в ЮKassa и возвращает (payment_id, confirmation_url).

    customer_email: обязателен для чека (54-ФЗ)
    telegram_user_id: кладём в metadata, чтобы webhook знал кому писать
    """
    idempotence_key = str(uuid.uuid4())

    payload = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": description,
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

    if telegram_user_id is not None:
        payload["metadata"] = {"telegram_user_id": str(int(telegram_user_id))}

    payment = Payment.create(payload, idempotence_key)
    return payment.id, payment.confirmation.confirmation_url


def get_payment_status(payment_id: str) -> str:
    payment = Payment.find_one(payment_id)
    return payment.status
