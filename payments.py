import os
import uuid

from dotenv import load_dotenv
from yookassa import Configuration, Payment

load_dotenv()

# ЮKassa креды из .env
Configuration.account_id = os.getenv("YOOKASSA_SHOP_ID")
Configuration.secret_key = os.getenv("YOOKASSA_SECRET_KEY")


def create_payment(amount_rub: str, description: str, return_url: str, customer_email: str):
    """
    Создаёт платёж в ЮKassa и возвращает (payment_id, confirmation_url).
    amount_rub: строкой, например "199.00"
    customer_email: обязателен для чека (54-ФЗ)
    """
    idempotence_key = str(uuid.uuid4())

    payment = Payment.create(
        {
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

                        # ВАЖНО: без этого у тебя и падало
                        "payment_subject": "service",
                        "payment_mode": "full_payment",
                    }
                ],
            },
        },
        idempotence_key,
    )

    # confirmation_url лежит в confirmation.confirmation_url
    return payment.id, payment.confirmation.confirmation_url


def get_payment_status(payment_id: str) -> str:
    """
    Возвращает статус платежа: pending / waiting_for_capture / succeeded / canceled и т.д.
    """
    payment = Payment.find_one(payment_id)
    return payment.status

