import os
from dotenv import load_dotenv
from payments import create_payment

load_dotenv()

payment_id, url = create_payment(
    amount_rub="10.00",
    description="TEST payment from local",
    return_url=os.getenv("YOOKASSA_RETURN_URL", "https://example.com/return")
)

print("payment_id:", payment_id)
print("pay_url:", url)
