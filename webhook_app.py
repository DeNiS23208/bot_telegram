import os
import sqlite3
import aiosqlite
import asyncio
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

# Запускаем фоновые задачи для проверки истекших платежей и подписок
@app.on_event("startup")
async def startup_event():
    """Запускаем фоновые задачи при старте приложения"""
    asyncio.create_task(check_expired_payments())
    asyncio.create_task(check_expired_subscriptions())
    asyncio.create_task(check_subscriptions_expiring_soon())
    print("✅ Фоновые задачи проверки истекших платежей и подписок запущены")


# Обработчик возврата с ЮKassa (если пользователь вернулся без оплаты)
@app.get("/payment/return")
async def payment_return(request: Request):
    """
    Обработчик возврата пользователя с формы оплаты ЮKassa
    Если пользователь вернулся без оплаты, проверяем статус и отправляем уведомление
    """
    # Получаем параметры из URL (ЮKassa может передавать payment_id или другие параметры)
    payment_id = request.query_params.get("payment_id") or request.query_params.get("orderId")
    
    print(f"📥 Получен возврат с формы оплаты: payment_id={payment_id}, query_params={dict(request.query_params)}")
    
    if payment_id:
        try:
            payment = Payment.find_one(payment_id)
            meta = payment.metadata or {}
            tg_user_id = meta.get("telegram_user_id")
            
            print(f"📋 Статус платежа {payment_id}: {payment.status}, tg_user_id: {tg_user_id}")
            
            if tg_user_id:
                tg_user_id = int(tg_user_id)
                
                # Если платеж все еще pending, значит пользователь не оплатил (вышел из формы)
                if payment.status == "pending":
                    # Проверяем, есть ли активная подписка
                    has_active = await has_active_subscription(tg_user_id)
                    
                    if not has_active:
                        # Отправляем уведомление о том, что оплата не была завершена
                        try:
                            await bot.send_message(
                                tg_user_id,
                                "❌ Платёж не был завершён\n\n"
                                "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                "Для оплаты доступа нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                            )
                            print(f"✅ Отправлено уведомление о незавершенной оплате пользователю {tg_user_id}")
                        except Exception as e:
                            print(f"❌ Ошибка отправки уведомления пользователю {tg_user_id}: {e}")
                    else:
                        print(f"ℹ️ Пользователь {tg_user_id} вернулся с формы оплаты, но у него уже есть активная подписка")
                
                # Если платеж отменен, webhook должен был обработать это, но на всякий случай проверяем
                elif payment.status == "canceled":
                    has_active = await has_active_subscription(tg_user_id)
                    if not has_active:
                        try:
                            await bot.send_message(
                                tg_user_id,
                                "❌ Платёж был отменён\n\n"
                                "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                "Для оплаты доступа нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                            )
                            print(f"✅ Отправлено уведомление об отмене платежа пользователю {tg_user_id} (через return)")
                        except Exception as e:
                            print(f"❌ Ошибка отправки уведомления пользователю {tg_user_id}: {e}")
                
        except Exception as e:
            print(f"❌ Ошибка обработки возврата: {e}")
            import traceback
            traceback.print_exc()
    
    # Возвращаем простую страницу или редирект
    return {"status": "ok", "message": "Вы вернулись с формы оплаты"}

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


async def has_active_subscription(telegram_id: int) -> bool:
    """Проверяет, есть ли у пользователя активная подписка"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        cursor = await db_conn.execute(
            "SELECT expires_at FROM subscriptions WHERE telegram_id = ?",
            (telegram_id,)
        )
        row = await cursor.fetchone()
        
        if not row or not row[0]:
            return False
        
        try:
            expires_at = datetime.fromisoformat(row[0])
            return expires_at > datetime.utcnow()
        except ValueError:
            return False


async def get_expired_pending_payments():
    """Получает список платежей со статусом pending, которые старше 10 минут"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        # Платежи старше 10 минут со статусом pending
        cutoff_time = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
        cursor = await db_conn.execute(
            """
            SELECT telegram_id, payment_id, created_at 
            FROM payments 
            WHERE status = 'pending' AND created_at < ?
            """,
            (cutoff_time,)
        )
        rows = await cursor.fetchall()
        return rows


async def get_expired_subscriptions():
    """Получает список подписок, которые истекли или истекают в ближайшие 3 дня"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        now = datetime.utcnow()
        # Подписки, которые истекли или истекают в течение 3 дней
        expires_soon = (now + timedelta(days=3)).isoformat()
        cursor = await db_conn.execute(
            """
            SELECT telegram_id, expires_at 
            FROM subscriptions 
            WHERE expires_at <= ? AND expires_at > ?
            """,
            (expires_soon, now.isoformat())
        )
        rows = await cursor.fetchall()
        return rows


async def get_subscriptions_expiring_soon():
    """Получает список подписок, которые истекают через 3 дня (для уведомления)"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        now = datetime.utcnow()
        # Подписки, которые истекают ровно через 3 дня (с небольшой погрешностью)
        target_date = now + timedelta(days=3)
        # Проверяем подписки, которые истекают в течение следующих 24 часов после 3 дней
        start_date = target_date.isoformat()
        end_date = (target_date + timedelta(hours=24)).isoformat()
        cursor = await db_conn.execute(
            """
            SELECT telegram_id, expires_at 
            FROM subscriptions 
            WHERE expires_at >= ? AND expires_at <= ?
            """,
            (start_date, end_date)
        )
        rows = await cursor.fetchall()
        return rows


async def check_expired_payments():
    """Проверяет истекшие платежи и уведомляет пользователей"""
    while True:
        try:
            await asyncio.sleep(60)  # Проверяем каждую минуту
            
            expired_payments = await get_expired_pending_payments()
            
            for telegram_id, payment_id, created_at in expired_payments:
                # Проверяем актуальный статус платежа в ЮKassa
                try:
                    payment = Payment.find_one(payment_id)
                    current_status = payment.status
                    
                    # Если платеж все еще pending (не оплачен), уведомляем пользователя
                    if current_status == "pending":
                        # ПРОВЕРЯЕМ: есть ли у пользователя активная подписка
                        has_active = await has_active_subscription(telegram_id)
                        
                        if has_active:
                            # Если подписка активна - просто обновляем статус, не отправляем уведомление
                            await update_payment_status_async(payment_id, "expired")
                            print(f"ℹ️ Платеж {payment_id} истек, но у пользователя {telegram_id} уже есть активная подписка - уведомление не отправлено")
                        else:
                            # Обновляем статус на expired
                            await update_payment_status_async(payment_id, "expired")
                            
                            # Уведомляем пользователя только если нет активной подписки
                            try:
                                await bot.send_message(
                                    telegram_id,
                                    "⏰ Срок действия ссылки на оплату истёк\n\n"
                                    "Вы открыли ссылку на оплату, но не завершили платёж.\n"
                                    "Ссылка была действительна 10 минут.\n\n"
                                    "Для оплаты доступа нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                                )
                                print(f"✅ Отправлено уведомление об истечении ссылки пользователю {telegram_id}")
                            except Exception as e:
                                print(f"❌ Ошибка отправки уведомления об истечении: {e}")
                    else:
                        # Если статус изменился (например, на canceled), обновляем в БД
                        await update_payment_status_async(payment_id, current_status)
                        
                except Exception as e:
                    print(f"❌ Ошибка проверки платежа {payment_id}: {e}")
                    
        except Exception as e:
            print(f"❌ Ошибка в фоновой задаче проверки платежей: {e}")
            await asyncio.sleep(60)  # Ждем перед следующей попыткой


async def check_subscriptions_expiring_soon():
    """Проверяет подписки, которые истекают через 3 дня, и отправляет уведомления"""
    notified_users = set()  # Чтобы не отправлять несколько раз одному пользователю
    
    while True:
        try:
            await asyncio.sleep(3600)  # Проверяем каждый час
            
            # Получаем подписки, которые истекают через 3 дня
            expiring_subs = await get_subscriptions_expiring_soon()
            
            for telegram_id, expires_at_str in expiring_subs:
                if telegram_id in notified_users:
                    continue
                    
                try:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    now = datetime.utcnow()
                    days_left = (expires_at - now).days
                    
                    # Если осталось примерно 3 дня (2-4 дня для учета погрешности)
                    if 2 <= days_left <= 4:
                        await bot.send_message(
                            telegram_id,
                            "⏰ Внимание! Подписка истекает через 3 дня\n\n"
                            f"Ваша подписка действует до: {expires_at.date()}\n\n"
                            "Для продления подписки нажмите кнопку 💳 Оплатить доступ.\n"
                            "Если подписка не будет продлена, вас удалят из канала."
                        )
                        notified_users.add(telegram_id)
                        print(f"✅ Отправлено уведомление о скором истечении подписки пользователю {telegram_id}")
                        
                except Exception as e:
                    print(f"❌ Ошибка обработки уведомления для пользователя {telegram_id}: {e}")
            
            # Очищаем обработанных пользователей раз в день
            if len(notified_users) > 100:
                notified_users.clear()
                    
        except Exception as e:
            print(f"❌ Ошибка в фоновой задаче проверки истекающих подписок: {e}")
            await asyncio.sleep(3600)


async def check_expired_subscriptions():
    """Проверяет истекшие подписки и отправляет ссылки на продление"""
    processed_users = set()  # Чтобы не отправлять несколько раз одному пользователю
    
    while True:
        try:
            await asyncio.sleep(3600)  # Проверяем каждый час
            
            # Проверяем подписки, которые истекли
            expired_subs = await get_expired_subscriptions()
            
            for telegram_id, expires_at_str in expired_subs:
                if telegram_id in processed_users:
                    continue
                    
                try:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    now = datetime.utcnow()
                    
                    # Если подписка уже истекла
                    if expires_at <= now:
                        # Создаем новую ссылку на оплату для продления
                        from payments import create_payment
                        
                        RETURN_URL_WEBHOOK = f"https://t.me/{os.getenv('BOT_USERNAME', 'work232_bot')}"
                        CUSTOMER_EMAIL = os.getenv("PAYMENT_CUSTOMER_EMAIL", "test@example.com")
                        
                        # create_payment - синхронная функция
                        payment_id, pay_url = create_payment(
                            amount_rub="1.00",
                            description="Продление подписки на канал (30 дней)",
                            return_url=RETURN_URL_WEBHOOK,
                            customer_email=CUSTOMER_EMAIL,
                            telegram_user_id=telegram_id,
                        )
                        
                        # Сохраняем платеж
                        async with aiosqlite.connect(DB_PATH) as db_conn:
                            await db_conn.execute(
                                "INSERT OR IGNORE INTO payments (telegram_id, payment_id, status, created_at) VALUES (?, ?, ?, ?)",
                                (telegram_id, payment_id, "pending", datetime.utcnow().isoformat())
                            )
                            await db_conn.commit()
                        
                        # Отправляем уведомление
                        await bot.send_message(
                            telegram_id,
                            "⏰ Ваша подписка истекла\n\n"
                            "Для продления подписки перейдите по ссылке:\n"
                            f"{pay_url}\n\n"
                            "После оплаты вернитесь в бота и нажмите: ✅ Проверить оплату"
                        )
                        
                        processed_users.add(telegram_id)
                        print(f"✅ Отправлена ссылка на продление подписки пользователю {telegram_id}")
                        
                except Exception as e:
                    print(f"❌ Ошибка обработки истекшей подписки для пользователя {telegram_id}: {e}")
            
            # Очищаем обработанных пользователей раз в день
            if len(processed_users) > 100:
                processed_users.clear()
                    
        except Exception as e:
            print(f"❌ Ошибка в фоновой задаче проверки подписок: {e}")
            await asyncio.sleep(3600)


# ================== YOOKASSA WEBHOOK ==================
@app.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        notification = WebhookNotificationFactory().create(data)
    except Exception as e:
        print(f"❌ Ошибка создания notification: {e}")
        raise HTTPException(status_code=400, detail="Bad YooKassa notification")

    payment_obj = notification.object
    payment_id = payment_obj.id
    event = notification.event
    
    # Логируем все события для отладки
    print(f"📥 Получено событие от ЮKassa: {event}, payment_id: {payment_id}")

    # Обрабатываем отмененные/неудачные платежи
    if event == "payment.canceled":
        print(f"🔄 Обработка canceled платежа: {payment_id}")
        try:
            payment = Payment.find_one(payment_id)
            meta = payment.metadata or {}
            tg_user_id = meta.get("telegram_user_id")
            
            print(f"📋 Метаданные платежа: {meta}, tg_user_id: {tg_user_id}")
            print(f"📋 Платеж из notification: {payment_obj}")
            
            if tg_user_id:
                tg_user_id = int(tg_user_id)
                # Обновляем статус платежа в БД
                await update_payment_status_async(payment_id, "canceled")
                
                # Определяем причину отмены
                cancellation_reason = "отменен пользователем"  # По умолчанию считаем, что пользователь отменил
                message_text = ""
                
                # Проверяем причину отмены из разных источников
                try:
                    # Сначала проверяем payment_obj из notification (может содержать актуальные данные)
                    cancellation_details_notification = None
                    if hasattr(payment_obj, 'cancellation_details'):
                        cancellation_details_notification = payment_obj.cancellation_details
                    elif hasattr(payment_obj, 'cancellationDetails'):
                        cancellation_details_notification = payment_obj.cancellationDetails
                    
                    # Затем проверяем payment из API
                    cancellation_details = None
                    if hasattr(payment, 'cancellation_details'):
                        cancellation_details = payment.cancellation_details
                    elif hasattr(payment, 'cancellationDetails'):
                        cancellation_details = payment.cancellationDetails
                    
                    # Используем данные из notification, если есть, иначе из API
                    cancellation_details_final = cancellation_details_notification or cancellation_details
                    
                    reason = ""
                    party = ""
                    
                    if cancellation_details_final:
                        # Пробуем разные способы получения данных
                        if isinstance(cancellation_details_final, dict):
                            reason = str(cancellation_details_final.get('reason', '')).lower()
                            party = str(cancellation_details_final.get('party', '')).lower()
                        else:
                            reason = str(getattr(cancellation_details_final, 'reason', '')).lower()
                            party = str(getattr(cancellation_details_final, 'party', '')).lower()
                        
                        print(f"🔍 Причина отмены: reason={reason}, party={party}, details={cancellation_details_final}")
                        
                        # Проверяем на недостаток средств (разные варианты)
                        if any(keyword in reason for keyword in ['insufficient', 'funds', 'недостаточно', 'money', 'balance']):
                            cancellation_reason = "недостаточно средств"
                            message_text = (
                                "❌ Недостаточно средств на карте\n\n"
                                "💳 Проверьте баланс карты и попробуйте еще раз пройти по ссылке на оплату.\n\n"
                                "Для оплаты нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                            )
                        elif any(keyword in party for keyword in ['user', 'merchant', 'yoo_money', 'payment_network']):
                            # Если party содержит user - значит пользователь отменил
                            if 'user' in party:
                                cancellation_reason = "отменен пользователем (выход из формы)"
                                message_text = (
                                    "❌ Платёж был отменён\n\n"
                                    "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                    "Для оплаты доступа нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                                )
                            else:
                                cancellation_reason = "отменен по другой причине"
                                message_text = (
                                    "❌ Платёж был отменён\n\n"
                                    "Оплата не была завершена.\n\n"
                                    "Для оплаты доступа нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                                )
                        elif any(keyword in reason for keyword in ['canceled_by_user', 'user_canceled', 'отменен', 'cancel']):
                            cancellation_reason = "отменен пользователем (выход из формы)"
                            message_text = (
                                "❌ Платёж был отменён\n\n"
                                "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                "Для оплаты доступа нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                            )
                        else:
                            # По умолчанию считаем, что пользователь отменил (выход из формы)
                            cancellation_reason = "отменен пользователем (выход из формы)"
                            message_text = (
                                "❌ Платёж был отменён\n\n"
                                "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                "Для оплаты доступа нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                            )
                    else:
                        # Если нет деталей, по умолчанию считаем что пользователь вышел из формы
                        cancellation_reason = "отменен пользователем (выход из формы)"
                        message_text = (
                            "❌ Платёж был отменён\n\n"
                            "Вы вышли из формы оплаты без завершения платежа.\n\n"
                            "Для оплаты доступа нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                        )
                    
                    # Если message_text пустой, используем стандартное сообщение
                    if not message_text:
                        cancellation_reason = "отменен пользователем (выход из формы)"
                        message_text = (
                            "❌ Платёж был отменён\n\n"
                            "Вы вышли из формы оплаты без завершения платежа.\n\n"
                            "Для оплаты доступа нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                        )
                        
                except Exception as e:
                    print(f"⚠️ Ошибка при определении причины отмены: {e}")
                    import traceback
                    traceback.print_exc()
                    # В случае ошибки все равно отправляем сообщение об отмене
                    cancellation_reason = "отменен пользователем (выход из формы)"
                    message_text = (
                        "❌ Платёж был отменён\n\n"
                        "Вы вышли из формы оплаты без завершения платежа.\n\n"
                        "Для оплаты доступа нажмите кнопку 💳 Оплатить доступ и перейдите по новой ссылке."
                    )
                
                # ПРОВЕРЯЕМ: есть ли у пользователя активная подписка
                has_active = await has_active_subscription(tg_user_id)
                
                if has_active:
                    # Если подписка активна - не отправляем уведомление об отмене старого платежа
                    print(f"ℹ️ Платеж {payment_id} отменен, но у пользователя {tg_user_id} уже есть активная подписка - уведомление не отправлено")
                else:
                    # Уведомляем пользователя только если нет активной подписки
                    try:
                        await bot.send_message(tg_user_id, message_text)
                        print(f"✅ Отправлено уведомление об отмене платежа пользователю {tg_user_id}, причина: {cancellation_reason}")
                    except Exception as e:
                        print(f"❌ Ошибка отправки уведомления об отмене платежа пользователю {tg_user_id}: {e}")
            else:
                print(f"⚠️ Нет telegram_user_id в метаданных платежа {payment_id}")
        except Exception as e:
            print(f"❌ Ошибка обработки canceled платежа {payment_id}: {e}")
            import traceback
            traceback.print_exc()
        
        return {"ok": True, "event": "payment.canceled"}

    # Обрабатываем возвраты (refunds)
    if event == "refund.succeeded":
        print(f"🔄 Обработка refund.succeeded: {payment_id}")
        try:
            # Получаем информацию о возврате
            refund_obj = notification.object
            payment_id_refund = refund_obj.payment_id if hasattr(refund_obj, 'payment_id') else None
            
            print(f"📋 Информация о возврате: payment_id={payment_id_refund}")
            
            if payment_id_refund:
                # Получаем оригинальный платеж
                payment = Payment.find_one(payment_id_refund)
                meta = payment.metadata or {}
                tg_user_id = meta.get("telegram_user_id")
                
                print(f"📋 Метаданные платежа: {meta}, tg_user_id: {tg_user_id}")
                
                if tg_user_id:
                    tg_user_id = int(tg_user_id)
                    
                    # Получаем сумму возврата
                    try:
                        if hasattr(refund_obj, 'amount'):
                            amount = refund_obj.amount.value if hasattr(refund_obj.amount, 'value') else str(refund_obj.amount.get('value', '0'))
                            currency = refund_obj.amount.currency if hasattr(refund_obj.amount, 'currency') else refund_obj.amount.get('currency', 'RUB')
                        else:
                            amount = "0"
                            currency = "RUB"
                    except Exception as e:
                        print(f"⚠️ Ошибка получения суммы возврата: {e}")
                        amount = "0"
                        currency = "RUB"
                    
                    # Отключаем подписку пользователя при возврате
                    try:
                        async with aiosqlite.connect(DB_PATH) as db_conn:
                            await db_conn.execute(
                                "DELETE FROM subscriptions WHERE telegram_id = ?",
                                (tg_user_id,)
                            )
                            await db_conn.execute(
                                "DELETE FROM approved_users WHERE telegram_user_id = ?",
                                (tg_user_id,)
                            )
                            await db_conn.commit()
                        print(f"✅ Подписка пользователя {tg_user_id} отменена из-за возврата")
                    except Exception as e:
                        print(f"⚠️ Ошибка отмены подписки: {e}")
                    
                    # Уведомляем пользователя о возврате
                    try:
                        await bot.send_message(
                            tg_user_id,
                            f"💰 Возврат средств выполнен\n\n"
                            f"Сумма возврата: {amount} {currency}\n"
                            f"ID платежа: {payment_id_refund}\n\n"
                            f"Ваша подписка была отменена.\n"
                            f"Деньги будут возвращены на карту в течение нескольких рабочих дней."
                        )
                        print(f"✅ Отправлено уведомление о возврате пользователю {tg_user_id}")
                    except Exception as e:
                        print(f"❌ Ошибка отправки уведомления о возврате пользователю {tg_user_id}: {e}")
                else:
                    print(f"⚠️ Нет telegram_user_id в метаданных платежа {payment_id_refund}")
            else:
                print(f"⚠️ Не удалось получить payment_id из возврата")
        except Exception as e:
            print(f"❌ Ошибка обработки refund.succeeded: {e}")
            import traceback
            traceback.print_exc()
        
        return {"ok": True, "event": "refund.succeeded"}

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

