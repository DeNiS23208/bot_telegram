import os
import aiosqlite
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import ChatJoinRequest
from yookassa import Payment, Configuration
from yookassa.domain.notification import WebhookNotificationFactory

from db import (
    DB_PATH,
    activate_subscription_days,
    update_payment_status,
    get_subscription_expires_at,
    ensure_user,
)

load_dotenv()

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID_STR = os.getenv("CHANNEL_ID")

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env")

if not CHANNEL_ID_STR:
    raise RuntimeError("CHANNEL_ID is missing in .env")

CHANNEL_ID = int(CHANNEL_ID_STR)

if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
    raise RuntimeError("YOOKASSA credentials missing in .env")

Configuration.account_id = YOOKASSA_SHOP_ID
Configuration.secret_key = YOOKASSA_SECRET_KEY

# ================== APP ==================
app = FastAPI()
bot = Bot(token=BOT_TOKEN)

# ================== DB HELPERS ==================
async def already_processed(payment_id: str) -> bool:
    """Проверяет, был ли уже обработан этот платёж"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Создаём таблицу для отслеживания обработанных платежей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS processed_payments (
                payment_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            )
        """)
        await db.commit()
        
        cur = await db.execute(
            "SELECT 1 FROM processed_payments WHERE payment_id = ?",
            (payment_id,)
        )
        row = await cur.fetchone()
        return row is not None


async def mark_processed(payment_id: str):
    """Отмечает платёж как обработанный"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS processed_payments (
                payment_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            )
        """)
        await db.execute(
            "INSERT OR IGNORE INTO processed_payments(payment_id, processed_at) VALUES (?, ?)",
            (payment_id, datetime.utcnow().isoformat())
        )
        await db.commit()


async def is_user_subscribed(tg_user_id: int) -> bool:
    """Проверяет, есть ли у пользователя активная подписка"""
    expires_at = await get_subscription_expires_at(tg_user_id)
    if not expires_at:
        return False
    
    return expires_at > datetime.utcnow()


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

    if await already_processed(payment_id):
        return {"ok": True, "duplicate": True}

    payment = Payment.find_one(payment_id)
    if payment.status != "succeeded":
        await mark_processed(payment_id)
        return {"ok": True, "ignored": payment.status}

    meta = payment.metadata or {}
    tg_user_id = meta.get("telegram_user_id")

    if not tg_user_id:
        await mark_processed(payment_id)
        return {"ok": True, "ignored": "no telegram_user_id"}

    tg_user_id = int(tg_user_id)

    # Обновляем статус платежа в БД
    await update_payment_status(payment_id, "succeeded")
    
    # Активируем подписку на 30 дней
    expires_at = await activate_subscription_days(tg_user_id, days=30)
    
    # Гарантируем, что пользователь есть в БД
    await ensure_user(tg_user_id, None)

    # Создаём персональную ссылку для вступления в канал
    invite = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        creates_join_request=True,
        expire_date=datetime.utcnow() + timedelta(hours=1)
    )

    await bot.send_message(
        tg_user_id,
        "✅ Оплата подтверждена!\n\n"
        "Ваша подписка активна до: {}\n\n"
        "Нажмите на ссылку ниже, отправьте заявку — бот автоматически одобрит её:\n"
        "{}\n\n"
        "Ссылка персональная и действует 1 час.".format(
            expires_at.date(),
            invite.invite_link
        )
    )

    await mark_processed(payment_id)
    return {"ok": True, "payment_id": payment_id, "user_id": tg_user_id}


# ================== JOIN REQUEST HANDLER ==================
@app.post("/telegram/join_request")
async def telegram_join_request(request: Request):
    """Обрабатывает заявки на вступление в канал"""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        chat_join = ChatJoinRequest(**data["chat_join_request"])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid chat_join_request")

    user_id = chat_join.from_user.id
    chat_id = chat_join.chat.id

    # Проверяем, есть ли у пользователя активная подписка
    if await is_user_subscribed(user_id):
        try:
            await bot.approve_chat_join_request(
                chat_id=chat_id,
                user_id=user_id
            )
            return {"ok": True, "approved": True, "user_id": user_id}
        except Exception as e:
            return {"ok": False, "error": str(e), "user_id": user_id}

    # Не одобряем — подписка не активна или истекла
    return {"ok": True, "approved": False, "reason": "no_active_subscription"}

