import os
import uuid
from yookassa import Configuration, Payment
from dotenv import load_dotenv

load_dotenv()

Configuration.account_id = os.getenv("YOOKASSA_SHOP_ID")
Configuration.secret_key = os.getenv("YOOKASSA_SECRET_KEY")


def create_payment(amount_rub: str, description: str, return_url: str):
    idempotence_key = str(uuid.uuid4())

    payment = Payment.create({
        "amount": {"value": amount_rub, "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": description,

        "receipt": {
            "customer": {
                "email": "test@example.com"
            },
            "items": [
                {
                    "description": "Доступ в закрытый Telegram-канал (30 дней)",
                    "quantity": "1.00",
                    "amount": {"value": amount_rub, "currency": "RUB"},
                    "vat_code": 1,
                    "payment_subject": "service",
                    "payment_mode": "full_payment"
                }
            ]
        }
    }, idempotence_key)

    return payment.id, payment.confirmation.confirmation_url


def get_payment_status(payment_id: str) -> str:
    p = Payment.find_one(payment_id)
    return p.status


print("SHOP_ID =", os.getenv("YOOKASSA_SHOP_ID"))
print("SECRET =", bool(os.getenv("YOOKASSA_SECRET_KEY")))
