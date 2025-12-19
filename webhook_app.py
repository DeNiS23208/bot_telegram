import os
import sqlite3
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from aiogram import Bot
from yookassa import Payment
from yookassa.domain.notification import WebhookNotificationFactory

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/opt/bot_telegram/bot.db")  # можешь поменять
CHANNEL_ID = os.getenv("CHANNEL_ID")  # пока не используем, пригодится позже

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")

app = FastAPI()
bot = Bot(token=BOT_TOKEN)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_payments (
            payment_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def already_processed(payment_id: str) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM processed_payments WHERE payment_id = ?", (payment_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def mark_processed(payment_id: str):
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO processed_payments(payment_id, processed_at) VALUES(?, ?)",
        (payment_id, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


@app.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    # 1) Берём JSON как dict (а не bytes->str)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 2) Пробуем распарсить как уведомление YooKassa
    try:
        notification = WebhookNotificationFactory().create(data)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad YooKassa notification")

    event = notification.event

    # Нас интересует только успешная оплата
    if event != "payment.succeeded":
        return {"ok": True}

    pay_obj = notification.object
    payment_id = getattr(pay_obj, "id", None)

    if not payment_id:
        raise HTTPException(status_code=400, detail="No payment id")

    # 3) защита от дублей (ЮKassa может прислать повтор)
    if already_processed(payment_id):
        return {"ok": True, "duplicate": True}

    # 4) Железная проверка у ЮKassa (не верим голому JSON)
    try:
        payment = Payment.find_one(payment_id)
    except Exception:
        # если ЮKassa временно недоступна, лучше вернуть 200 и обработать повтором позже,
        # но для простоты сейчас отдадим 500
        raise HTTPException(status_code=500, detail="Failed to verify payment")

    if payment.status != "succeeded":
        return {"ok": True, "ignored": f"status={payment.status}"}

    meta = payment.metadata or {}
    tg_user_id = meta.get("telegram_user_id")

    # Если не знаем кому выдавать, просто помечаем и выходим
    if not tg_user_id:
        mark_processed(payment_id)
        return {"ok": True, "ignored": "no telegram_user_id"}

    # 5) подтверждаем пользователю
    try:
        await bot.send_message(int(tg_user_id), "✅ Оплата подтверждена. Готовим доступ…")
    except Exception:
        # если не смогли написать пользователю, всё равно пометим платёж,
        # иначе будет вечный дубль
        mark_processed(payment_id)
        return {"ok": True, "warn": "cannot message user"}

    mark_processed(payment_id)
    return {"ok": True, "payment_id": payment_id}

