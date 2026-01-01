#!/usr/bin/env python3
"""
Скрипт для создания возврата средств по платежу

Использование:
    python3 create_refund.py <payment_id> [amount] [description]

Примеры:
    # Полный возврат
    python3 create_refund.py 2c8d8c8e-0001-5000-8000-000000000000

    # Частичный возврат
    python3 create_refund.py 2c8d8c8e-0001-5000-8000-000000000000 500.00

    # Возврат с описанием
    python3 create_refund.py 2c8d8c8e-0001-5000-8000-000000000000 500.00 "Возврат по запросу клиента"
"""

import sys
import os
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

from payments import create_refund

def main():
    if len(sys.argv) < 2:
        print("❌ Ошибка: не указан payment_id")
        print("\nИспользование:")
        print("  python3 create_refund.py <payment_id> [amount] [description]")
        print("\nПримеры:")
        print("  # Полный возврат")
        print("  python3 create_refund.py 2c8d8c8e-0001-5000-8000-000000000000")
        print("\n  # Частичный возврат")
        print("  python3 create_refund.py 2c8d8c8e-0001-5000-8000-000000000000 500.00")
        print("\n  # Возврат с описанием")
        print("  python3 create_refund.py 2c8d8c8e-0001-5000-8000-000000000000 500.00 \"Возврат по запросу клиента\"")
        sys.exit(1)
    
    payment_id = sys.argv[1]
    amount_rub = sys.argv[2] if len(sys.argv) > 2 else None
    description = sys.argv[3] if len(sys.argv) > 3 else None
    
    try:
        print(f"🔄 Создание возврата для платежа {payment_id}...")
        if amount_rub:
            print(f"💰 Сумма возврата: {amount_rub} RUB")
        else:
            print("💰 Сумма возврата: полная (вся сумма платежа)")
        if description:
            print(f"📝 Описание: {description}")
        
        refund_id, status = create_refund(
            payment_id=payment_id,
            amount_rub=amount_rub,
            description=description
        )
        
        print(f"\n✅ Возврат успешно создан!")
        print(f"🆔 ID возврата: {refund_id}")
        print(f"📊 Статус: {status}")
        print(f"\n💡 Примечание: Возврат работает для всех типов платежей (SberPay, СБП, банковская карта)")
        print(f"   Деньги будут возвращены на карту/счет в течение нескольких рабочих дней.")
        
    except ValueError as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Неожиданная ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

