import os
import sqlite3
import aiosqlite
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invite_links (
            invite_link TEXT PRIMARY KEY,
            telegram_user_id INTEGER NOT NULL,
            payment_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked INTEGER DEFAULT 0,
            FOREIGN KEY (telegram_user_id) REFERENCES approved_users(telegram_user_id)
        )
    """)
    # Создаем таблицы для подписок и платежей (если их нет)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            telegram_id INTEGER PRIMARY KEY,
            expires_at TEXT,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            payment_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
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


def save_invite_link(invite_link: str, telegram_user_id: int, payment_id: str):
    """Сохраняет информацию о созданной ссылке-приглашении"""
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO invite_links(invite_link, telegram_user_id, payment_id, created_at) VALUES (?, ?, ?, ?)",
        (invite_link, telegram_user_id, payment_id, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def revoke_invite_link(invite_link: str):
    """Помечает ссылку как отозванную"""
    conn = db()
    conn.execute(
        "UPDATE invite_links SET revoked = 1 WHERE invite_link = ?",
        (invite_link,)
    )
    conn.commit()
    conn.close()


async def activate_subscription(telegram_id: int, days: int = 30):
    """Активирует подписку на N дней (асинхронная версия для webhook)"""
    expires_at = datetime.utcnow() + timedelta(days=days)
    
    async with aiosqlite.connect(DB_PATH) as db_conn:
        # гарантируем, что юзер существует
        await db_conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, created_at) VALUES (?, ?, ?)",
            (telegram_id, None, datetime.utcnow().isoformat())
        )
        
        # upsert подписки
        await db_conn.execute(
            """
            INSERT INTO subscriptions (telegram_id, expires_at)
            VALUES (?, ?) ON CONFLICT(telegram_id) DO
            UPDATE SET expires_at=excluded.expires_at
            """,
            (telegram_id, expires_at.isoformat())
        )
        await db_conn.commit()


async def update_payment_status_async(payment_id: str, status: str):
    """Обновляет статус платежа (асинхронная версия)"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute(
            "UPDATE payments SET status = ? WHERE payment_id = ?",
            (status, payment_id)
        )
        await db_conn.commit()


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

    payment_obj = notification.object
    payment_id = payment_obj.id
    event = notification.event

    # Обрабатываем отмененные/неудачные платежи
    if event == "payment.canceled":
        payment = Payment.find_one(payment_id)
        meta = payment.metadata or {}
        tg_user_id = meta.get("telegram_user_id")
        
        if tg_user_id:
            tg_user_id = int(tg_user_id)
            # Обновляем статус платежа в БД
            await update_payment_status_async(payment_id, "canceled")
            
            # Уведомляем пользователя
            try:
                await bot.send_message(
                    tg_user_id,
                    "❌ Платёж не был завершён\n\n"
                    "Оплата была отменена или не прошла.\n"
                    "Возможные причины:\n"
                    "• Недостаточно средств на карте\n"
                    "• Операция была отменена\n"
                    "• Истекло время ожидания оплаты\n\n"
                    "Вы можете попробовать оплатить снова, нажав кнопку 💳 Оплатить доступ."
                )
            except Exception as e:
                print(f"Ошибка отправки уведомления об отмене платежа: {e}")
        
        return {"ok": True, "event": "payment.canceled"}

    # Обрабатываем успешные платежи
    if event != "payment.succeeded":
        return {"ok": True, "event": event}

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
    
    # Активируем подписку на 30 дней
    await activate_subscription(tg_user_id, days=30)
    
    # Обновляем статус платежа в БД
    await update_payment_status_async(payment_id, "succeeded")

    # Сначала проверяем и разбаниваем пользователя, если он был забанен
    try:
        await bot.unban_chat_member(
            chat_id=CHANNEL_ID,
            user_id=tg_user_id,
            only_if_banned=True  # Разбаниваем только если был забанен
        )
    except Exception:
        pass  # Игнорируем ошибки разбана

    # Пытаемся создать ссылку БЕЗ заявки (прямой доступ)
    invite_link = None
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            creates_join_request=False,  # БЕЗ заявки - прямой доступ
            member_limit=1,  # Одноразовая ссылка
            expire_date=datetime.utcnow() + timedelta(hours=24)
        )
        invite_link = invite.invite_link
        print(f"✅ Создана ссылка БЕЗ заявки для пользователя {tg_user_id}")
    except Exception as e:
        # Если не получилось (канал требует одобрения), создаем ссылку с заявкой
        # Заявка будет автоматически одобрена через обработчик в bot.py
        print(f"⚠️ Не удалось создать ссылку без заявки: {e}. Создаю ссылку с заявкой.")
        try:
            invite = await bot.create_chat_invite_link(
                chat_id=CHANNEL_ID,
                creates_join_request=True,  # С заявкой, но она будет автоматически одобрена
                member_limit=1,
                expire_date=datetime.utcnow() + timedelta(hours=24)
            )
            invite_link = invite.invite_link
        except Exception as e2:
            print(f"❌ Ошибка создания ссылки: {e2}")
            # Отправляем сообщение об ошибке
            await bot.send_message(
                tg_user_id,
                "✅ Оплата подтверждена!\n\n"
                "Произошла ошибка при создании ссылки. Пожалуйста, свяжитесь с администратором."
            )
            mark_processed(payment_id)
            return {"ok": True, "error": "failed to create invite link"}

    # Сохраняем информацию о ссылке в БД
    if invite_link:
        save_invite_link(invite_link, tg_user_id, payment_id)
        
        # Получаем дату окончания подписки для отображения
        expires_at = datetime.utcnow() + timedelta(days=30)

        await bot.send_message(
            tg_user_id,
            "✅ Оплата подтверждена!\n\n"
            f"Подписка активна до: {expires_at.date()}\n\n"
            "Нажмите на ссылку ниже, чтобы попасть в канал:\n"
            f"{invite_link}\n\n"
            "⚠️ Ссылка одноразовая и персональная. Заявка будет одобрена автоматически."
        )

    mark_processed(payment_id)
    return {"ok": True, "payment_id": payment_id}


# ================== TELEGRAM WEBHOOK (для получения обновлений от Telegram) ==================
@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Обработчик webhook от Telegram для получения обновлений (включая заявки на вступление)
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Обрабатываем заявку на вступление в канал
    if "chat_join_request" in data:
        try:
            from aiogram.types import Update
            update = Update(**data)
            
            if update.chat_join_request:
                chat_join = update.chat_join_request
                user_id = chat_join.from_user.id
                chat_id = chat_join.chat.id

                # Если пользователь оплатил - автоматически одобряем заявку
                if is_user_allowed(user_id) and chat_id == CHANNEL_ID:
                    try:
                        await bot.approve_chat_join_request(
                            chat_id=chat_id,
                            user_id=user_id
                        )
                        return {"ok": True, "approved": True}
                    except Exception as e:
                        # Логируем ошибку, но не падаем
                        print(f"Error approving join request: {e}")
                        return {"ok": True, "approved": False, "error": str(e)}
                else:
                    # Пользователь не оплатил или это не наш канал
                    return {"ok": True, "approved": False}
        except Exception as e:
            print(f"Error processing chat_join_request: {e}")
            return {"ok": True, "error": str(e)}

    return {"ok": True}


# ================== JOIN REQUEST HANDLER (старый формат, для совместимости) ==================
@app.post("/telegram/join_request")
async def telegram_join_request(request: Request):
    """
    Старый обработчик заявок (для обратной совместимости)
    """
    try:
        data = await request.json()
        
        # Проверяем разные форматы данных
        if "chat_join_request" in data:
            chat_join_data = data["chat_join_request"]
        elif isinstance(data, dict) and "from_user" in data:
            chat_join_data = data
        else:
            return {"ok": True, "ignored": "unknown format"}

        user_id = chat_join_data.get("from_user", {}).get("id") or chat_join_data.get("user", {}).get("id")
        chat_id = chat_join_data.get("chat", {}).get("id")

        if not user_id:
            return {"ok": True, "ignored": "no user_id"}

        user_id = int(user_id)

        # Если пользователь оплатил - автоматически одобряем заявку
        if is_user_allowed(user_id) and (not chat_id or int(chat_id) == CHANNEL_ID):
            try:
                await bot.approve_chat_join_request(
                    chat_id=chat_id or CHANNEL_ID,
                    user_id=user_id
                )
                return {"ok": True, "approved": True}
            except Exception as e:
                print(f"Error approving join request: {e}")
                return {"ok": True, "approved": False, "error": str(e)}

        return {"ok": True, "approved": False}
    except Exception as e:
        print(f"Error in join_request handler: {e}")
        return {"ok": True, "error": str(e)}

