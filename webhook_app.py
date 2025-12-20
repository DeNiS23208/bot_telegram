import os
import sqlite3
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import ChatJoinRequest
from yookassa import Payment, Configuration
from yookassa.domain.notification import WebhookNotificationFactory

load_dotenv()

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/opt/bot_telegram/bot.db")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")

if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
    raise RuntimeError("YOOKASSA credentials missing in .env")

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# ================== APP ==================
app = FastAPI()
bot = Bot(token=BOT_TOKEN)

# ================== DB ==================
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_payments (
            payment_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS approved_users (
            telegram_user_id INTEGER PRIMARY KEY,
            approved_at TEXT NOT NULL
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
        "INSERT OR IGNORE INTO processed_payments(payment_id, processed_at) VALUES (?, ?)",
        (payment_id, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def allow_user(tg_user_id: int):
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO approved_users(telegram_user_id, approved_at) VALUES (?, ?)",
        (tg_user_id, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def is_user_allowed(tg_user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM approved_users WHERE telegram_user_id = ?",
        (tg_user_id,)
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


# ================== YOOKASSA WEBHOOK ==================
@app.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        notification = WebhookNotificationFactory().create(data)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad YooKassa notification")

    if notification.event != "payment.succeeded":
        return {"ok": True}

    payment_obj = notification.object
    payment_id = payment_obj.id

    if already_processed(payment_id):
        return {"ok": True, "duplicate": True}

    payment = Payment.find_one(payment_id)
    if payment.status != "succeeded":
        return {"ok": True, "ignored": payment.status}

    meta = payment.metadata or {}
    tg_user_id = meta.get("telegram_user_id")

    if not tg_user_id:
        mark_processed(payment_id)
        return {"ok": True, "ignored": "no telegram_user_id"}

    tg_user_id = int(tg_user_id)

    # разрешаем пользователю вступление
    allow_user(tg_user_id)

    # создаём ссылку ТОЛЬКО для заявки
    invite = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        creates_join_request=True,
        expire_date=datetime.utcnow() + timedelta(hours=1)
    )

    await bot.send_message(
        tg_user_id,
        "✅ Оплата подтверждена.\n\n"
        "Нажмите на ссылку ниже, отправьте заявку — бот автоматически одобрит её:\n"
        f"{invite.invite_link}\n\n"
        "Ссылка персональная и действует 1 час."
    )

    mark_processed(payment_id)
    return {"ok": True, "payment_id": payment_id}


# ================== JOIN REQUEST HANDLER ==================
@app.post("/telegram/join_request")
async def telegram_join_request(request: Request):
    data = await request.json()
    chat_join = ChatJoinRequest(**data["chat_join_request"])

    user_id = chat_join.from_user.id

    if is_user_allowed(user_id):
        await bot.approve_chat_join_request(
            chat_id=chat_join.chat.id,
            user_id=user_id
        )
        return {"ok": True, "approved": True}

    # не одобряем — человек не платил
    return {"ok": True, "approved": False}

