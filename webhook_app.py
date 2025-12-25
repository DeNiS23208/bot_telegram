import os
import sqlite3
import aiosqlite
import asyncio
from datetime import datetime, timedelta
import logging

from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import ChatJoinRequest, ReplyKeyboardMarkup, KeyboardButton
from yookassa import Payment, Configuration
from yookassa.domain.notification import WebhookNotificationFactory

from utils import format_datetime_moscow
from config import (
    PAYMENT_LINK_VALID_MINUTES,
    SUBSCRIPTION_DAYS,
    SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS,
    SUBSCRIPTION_EXPIRING_NOTIFICATION_WINDOW_HOURS,
    CHECK_EXPIRED_PAYMENTS_INTERVAL_SECONDS,
    CHECK_EXPIRED_SUBSCRIPTIONS_INTERVAL_SECONDS,
    CHECK_EXPIRING_SUBSCRIPTIONS_INTERVAL_SECONDS,
    MAX_NOTIFIED_USERS_CACHE_SIZE,
    PAYMENT_AMOUNT_RUB,
)
from db import is_user_allowed

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
    logger.info("✅ Фоновые задачи проверки истекших платежей и подписок запущены")


# Обработчик возврата с ЮKassa (если пользователь вернулся без оплаты)
@app.get("/payment/return")
async def payment_return(request: Request):
    """
    Обработчик возврата пользователя с формы оплаты ЮKassa
    Если пользователь вернулся без оплаты, проверяем статус и отправляем уведомление
    """
    # Получаем telegram_user_id из query параметров (передается в return_url)
    tg_user_id_param = request.query_params.get("user_id")
    # Также пытаемся получить payment_id напрямую (если ЮKassa передает)
    payment_id = request.query_params.get("payment_id") or request.query_params.get("orderId")
    
    logger.info(f"📥 Получен возврат с формы оплаты: user_id={tg_user_id_param}, payment_id={payment_id}, query_params={dict(request.query_params)}")
    
    tg_user_id = None
    
    # Если есть payment_id, получаем tg_user_id из метаданных платежа
    if payment_id:
        try:
            payment = Payment.find_one(payment_id)
            meta = payment.metadata or {}
            tg_user_id = meta.get("telegram_user_id")
            logger.info(f"📋 Получен tg_user_id из метаданных платежа: {tg_user_id}")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка получения платежа {payment_id}: {e}")
    
    # Если tg_user_id не получен из платежа, используем из параметров
    if not tg_user_id and tg_user_id_param:
        try:
            tg_user_id = int(tg_user_id_param)
            logger.info(f"📋 Использован tg_user_id из параметров: {tg_user_id}")
        except ValueError:
            logger.warning(f"⚠️ Неверный формат user_id: {tg_user_id_param}")
    
    if tg_user_id:
        tg_user_id = int(tg_user_id)
        
        # Если нет payment_id, находим последний pending платеж пользователя
        if not payment_id:
            try:
                from db import get_active_pending_payment
                active_payment = await get_active_pending_payment(tg_user_id, minutes=PAYMENT_LINK_VALID_MINUTES * 3)
                if active_payment:
                    payment_id, created_at = active_payment
                    logger.info(f"📋 Найден последний pending платеж: {payment_id}")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка поиска последнего платежа: {e}")
                payment_id = None
        
        # Если есть payment_id, проверяем статус платежа
        if payment_id:
            try:
                payment = Payment.find_one(payment_id)
                current_status = payment.status
                logger.info(f"📋 Статус платежа {payment_id}: {current_status}")
                
                # Если платеж все еще pending, значит пользователь не оплатил (вышел из формы)
                if current_status == "pending":
                    # Проверяем, есть ли активная подписка
                    has_active = await has_active_subscription(tg_user_id)
                    
                    if not has_active:
                        # Не отправляем уведомление, если пользователь сам вышел из формы
                        # Просто помечаем платеж как обработанный, чтобы фоновая задача не отправляла уведомление об истечении
                        # Но НЕ меняем статус на canceled, чтобы можно было оплатить позже
                        logger.info(f"ℹ️ Пользователь {tg_user_id} вернулся с формы оплаты (платеж pending) - уведомление не отправлено")
                    else:
                        logger.info(f"ℹ️ Пользователь {tg_user_id} вернулся с формы оплаты, но у него уже есть активная подписка")
                
                # Если платеж отменен, webhook должен был обработать это
                elif current_status == "canceled":
                    has_active = await has_active_subscription(tg_user_id)
                    if not has_active:
                        # Уже должен был быть обработан через webhook, но на всякий случай проверим
                        logger.info(f"ℹ️ Платеж {payment_id} уже отменен, webhook должен был обработать")
                        
            except Exception as e:
                logger.error(f"❌ Ошибка проверки платежа {payment_id}: {e}")
                import traceback
                traceback.print_exc()
        else:
            # Если не нашли платеж, не отправляем уведомление (пользователь сам вышел)
            logger.info(f"ℹ️ Пользователь {tg_user_id} вернулся с формы оплаты (платеж не найден) - уведомление не отправлено")
    else:
        logger.warning(f"⚠️ Не удалось определить telegram_user_id для обработки возврата")
    
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
            starts_at TEXT,
            auto_renewal_enabled INTEGER DEFAULT 0,
            saved_payment_method_id TEXT,
            subscription_expired_notified INTEGER DEFAULT 0,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        )
    """)
    # Добавляем колонку subscription_expired_notified, если её нет (для существующих БД)
    try:
        conn.execute("ALTER TABLE subscriptions ADD COLUMN subscription_expired_notified INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass  # Колонка уже существует
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


async def get_main_menu_for_user(telegram_id: int) -> ReplyKeyboardMarkup:
    """Создает главное меню для пользователя с учетом статуса подписки"""
    # Константы кнопок (должны совпадать с bot.py)
    BTN_PAY_1 = "💳 Получить доступ"
    BTN_MANAGE_SUB = "⚙️ Управление доступом"
    BTN_STATUS_1 = "📊 Статус доступа"
    BTN_ABOUT_1 = "ℹ️ О проекте"
    BTN_CHECK_1 = "🔍 Проверить оплату"
    BTN_SUPPORT = "💬 Поддержка"
    
    # Проверяем наличие активной подписки
    from db import get_subscription_expires_at
    expires_at = await get_subscription_expires_at(telegram_id)
    now = datetime.utcnow()
    has_active_subscription = expires_at and expires_at > now
    
    # Если есть активная подписка - показываем "Управление доступом", иначе "Получить доступ"
    payment_button = BTN_MANAGE_SUB if has_active_subscription else BTN_PAY_1
    
    keyboard = [
        [KeyboardButton(text=payment_button)],
        [KeyboardButton(text=BTN_STATUS_1)],
        [KeyboardButton(text=BTN_ABOUT_1)],
        [KeyboardButton(text=BTN_CHECK_1)],
        [KeyboardButton(text=BTN_SUPPORT)],
    ]
    
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
    )


async def activate_subscription(telegram_id: int, days: int = 30) -> tuple[datetime, datetime]:
    """Активирует подписку на N дней (асинхронная версия для webhook)
    Возвращает (starts_at, expires_at)"""
    starts_at = datetime.utcnow()
    expires_at = starts_at + timedelta(days=days)
    
    async with aiosqlite.connect(DB_PATH) as db_conn:
        # гарантируем, что юзер существует
        await db_conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, created_at) VALUES (?, ?, ?)",
            (telegram_id, None, datetime.utcnow().isoformat())
        )
        
        # upsert подписки (сохраняем дату начала и окончания)
        # При активации новой подписки сбрасываем флаг subscription_expired_notified
        await db_conn.execute(
            """
            INSERT INTO subscriptions (telegram_id, expires_at, starts_at, subscription_expired_notified)
            VALUES (?, ?, ?, 0) ON CONFLICT(telegram_id) DO
            UPDATE SET expires_at=excluded.expires_at, starts_at=excluded.starts_at,
                       subscription_expired_notified=0
            """,
            (telegram_id, expires_at.isoformat(), starts_at.isoformat())
        )
        await db_conn.commit()
    
    return starts_at, expires_at


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
    """Получает список платежей со статусом pending, которые старше N минут"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        # Платежи старше N минут со статусом pending (НЕ canceled и НЕ expired)
        cutoff_time = (datetime.utcnow() - timedelta(minutes=PAYMENT_LINK_VALID_MINUTES)).isoformat()
        cursor = await db_conn.execute(
            """
            SELECT telegram_id, payment_id, created_at 
            FROM payments 
            WHERE status = 'pending' 
            AND created_at < ?
            AND created_at > ?
            """,
            (cutoff_time, (datetime.utcnow() - timedelta(hours=24)).isoformat())  # Только за последние 24 часа
        )
        rows = await cursor.fetchall()
        return rows


async def get_expired_subscriptions():
    """Получает список подписок, которые истекли"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        now = datetime.utcnow()
        now_iso = now.isoformat()
        # Подписки, которые уже истекли (проверяем с небольшим запасом для точности)
        cursor = await db_conn.execute(
            """
            SELECT telegram_id, expires_at, auto_renewal_enabled, saved_payment_method_id
            FROM subscriptions 
            WHERE expires_at IS NOT NULL 
            AND expires_at <= ?
            """,
            (now_iso,)
        )
        rows = await cursor.fetchall()
        logger.debug(f"🔍 get_expired_subscriptions: найдено {len(rows)} истекших подписок (now={now_iso})")
        for row in rows:
            logger.debug(f"  - Пользователь {row[0]}: expires_at={row[1]}, auto_renewal={row[2]}, saved_method={bool(row[3]) if row[3] else False}")
        return rows


async def get_subscriptions_expiring_soon():
    """Получает список подписок, которые истекают через N дней (для уведомления)"""
    async with aiosqlite.connect(DB_PATH) as db_conn:
        now = datetime.utcnow()
        # Подписки, которые истекают через N дней (с небольшой погрешностью)
        target_date = now + timedelta(days=SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS)
        # Проверяем подписки, которые истекают в течение окна уведомления
        start_date = target_date.isoformat()
        end_date = (target_date + timedelta(hours=SUBSCRIPTION_EXPIRING_NOTIFICATION_WINDOW_HOURS)).isoformat()
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
    notified_payments = set()  # Отслеживаем, для каких платежей уже отправлено уведомление
    
    while True:
        try:
            await asyncio.sleep(CHECK_EXPIRED_PAYMENTS_INTERVAL_SECONDS)
            
            expired_payments = await get_expired_pending_payments()
            
            for telegram_id, payment_id, created_at in expired_payments:
                # Пропускаем, если уведомление уже было отправлено для этого платежа
                if payment_id in notified_payments:
                    continue
                
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
                            notified_payments.add(payment_id)  # Помечаем как обработанный
                            logger.info(f"ℹ️ Платеж {payment_id} истек, но у пользователя {telegram_id} уже есть активная подписка - уведомление не отправлено")
                        else:
                            # Обновляем статус на expired
                            await update_payment_status_async(payment_id, "expired")
                            
                            # Уведомляем пользователя только если нет активной подписки (ОДИН РАЗ)
                            try:
                                await bot.send_message(
                                    telegram_id,
                                    f"⏰ Срок действия ссылки на оплату истёк\n\n"
                                    "Вы открыли ссылку на оплату, но не завершили платёж.\n"
                                    f"Ссылка была действительна {PAYMENT_LINK_VALID_MINUTES} минут.\n\n"
                                    "Для оплаты доступа нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                                )
                                notified_payments.add(payment_id)  # Помечаем, что уведомление отправлено
                                logger.info(f"✅ Отправлено уведомление об истечении ссылки пользователю {telegram_id} для платежа {payment_id} (один раз)")
                            except Exception as e:
                                logger.error(f"❌ Ошибка отправки уведомления об истечении: {e}")
                    else:
                        # Если статус изменился (например, на canceled), обновляем в БД
                        await update_payment_status_async(payment_id, current_status)
                        notified_payments.add(payment_id)  # Помечаем как обработанный
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка проверки платежа {payment_id}: {e}")
            
            # Очищаем старые записи из notified_payments (старше 24 часов), чтобы не накапливать память
            # Но это не критично, так как payment_id уникальны
            if len(notified_payments) > 1000:
                # Если накопилось слишком много, очищаем (в реальности это маловероятно)
                notified_payments.clear()
                logger.info("🧹 Очищен кэш notified_payments")
                    
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче проверки платежей: {e}")
            await asyncio.sleep(60)  # Ждем перед следующей попыткой


async def check_subscriptions_expiring_soon():
    """Проверяет подписки, которые истекают через N дней, и отправляет уведомления"""
    notified_users = set()  # Чтобы не отправлять несколько раз одному пользователю
    
    while True:
        try:
            await asyncio.sleep(CHECK_EXPIRING_SUBSCRIPTIONS_INTERVAL_SECONDS)
            
            # Получаем подписки, которые истекают через N дней
            expiring_subs = await get_subscriptions_expiring_soon()
            
            for telegram_id, expires_at_str in expiring_subs:
                if telegram_id in notified_users:
                    continue
                    
                try:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    now = datetime.utcnow()
                    days_left = (expires_at - now).days
                    
                    # Если осталось примерно N дней (с погрешностью ±1 день)
                    notification_days_min = SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS - 1
                    notification_days_max = SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS + 1
                    if notification_days_min <= days_left <= notification_days_max:
                        await bot.send_message(
                            telegram_id,
                            f"⏰ Внимание! Доступ истекает через {SUBSCRIPTION_EXPIRING_NOTIFICATION_DAYS} дня\n\n"
                            f"Ваш доступ действует до: {expires_at.date()}\n\n"
                            "Для продления доступа нажмите кнопку 💳 Получить доступ.\n"
                            "Если доступ не будет продлен, вас удалят из канала."
                        )
                        notified_users.add(telegram_id)
                        logger.info(f"✅ Отправлено уведомление о скором истечении подписки пользователю {telegram_id}")
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки уведомления для пользователя {telegram_id}: {e}")
            
            # Очищаем обработанных пользователей при достижении лимита
            if len(notified_users) > MAX_NOTIFIED_USERS_CACHE_SIZE:
                notified_users.clear()
                    
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче проверки истекающих подписок: {e}")
            await asyncio.sleep(CHECK_EXPIRING_SUBSCRIPTIONS_INTERVAL_SECONDS)


async def check_expired_subscriptions():
    """Проверяет истекшие подписки и выполняет автопродление или отправляет ссылку на оплату"""
    processed_users = {}  # {telegram_id: timestamp} - чтобы не отправлять несколько раз одному пользователю в течение короткого времени
    
    while True:
        try:
            await asyncio.sleep(CHECK_EXPIRED_SUBSCRIPTIONS_INTERVAL_SECONDS)
            
            # Очищаем processed_users от записей старше 5 минут (чтобы можно было повторить попытку)
            now = datetime.utcnow()
            expired_processed = [uid for uid, ts in processed_users.items() if (now - ts).total_seconds() > 300]
            for uid in expired_processed:
                del processed_users[uid]
                logger.info(f"🔄 Удален пользователь {uid} из processed_users (прошло более 5 минут)")
            
            # Проверяем подписки, которые истекли
            expired_subs = await get_expired_subscriptions()
            
            logger.info(f"🔍 Проверка истекших подписок: найдено {len(expired_subs)} подписок")
            
            for row in expired_subs:
                telegram_id = row[0]
                expires_at_str = row[1]
                auto_renewal_enabled = bool(row[2]) if len(row) > 2 else False
                saved_payment_method_id = row[3] if len(row) > 3 and row[3] else None
                
                logger.info(f"📋 Обработка подписки пользователя {telegram_id}: expires_at={expires_at_str}, auto_renewal={auto_renewal_enabled}, saved_method={bool(saved_payment_method_id)}")
                
                # Проверяем, был ли пользователь обработан недавно (в течение последних 2 минут)
                if telegram_id in processed_users:
                    time_since_processed = (now - processed_users[telegram_id]).total_seconds()
                    if time_since_processed < 120:  # 2 минуты
                        logger.info(f"⏭️ Пользователь {telegram_id} уже обработан {time_since_processed:.0f} секунд назад, пропускаем")
                        continue
                    else:
                        # Удаляем из processed_users, если прошло больше 2 минут
                        del processed_users[telegram_id]
                        logger.info(f"🔄 Пользователь {telegram_id} был обработан {time_since_processed:.0f} секунд назад, повторяем попытку")
                    
                try:
                    expires_at = datetime.fromisoformat(expires_at_str)
                    now = datetime.utcnow()
                    
                    logger.info(f"⏰ Пользователь {telegram_id}: expires_at={expires_at}, now={now}, разница={(now - expires_at).total_seconds()} секунд")
                    
                    # Если подписка уже истекла
                    if expires_at <= now:
                        auto_payment_failed = False
                        
                        # Проверяем, включено ли автопродление и есть ли сохраненный способ оплаты
                        if auto_renewal_enabled and saved_payment_method_id:
                            # Пытаемся выполнить автоматическое списание
                            try:
                                from payments import create_auto_payment, get_payment_status
                                from db import activate_subscription_days, save_payment, update_payment_status
                                
                                CUSTOMER_EMAIL = os.getenv("PAYMENT_CUSTOMER_EMAIL", "test@example.com")
                                
                                # Создаем автоматический платеж
                                payment_id, payment_status = create_auto_payment(
                                    amount_rub=PAYMENT_AMOUNT_RUB,
                                    description=f"Автопродление подписки на канал ({SUBSCRIPTION_DAYS * 1440:.0f} минут)",
                                    customer_email=CUSTOMER_EMAIL,
                                    telegram_user_id=telegram_id,
                                    payment_method_id=saved_payment_method_id,
                                )
                                
                                # Сохраняем платеж
                                await save_payment(telegram_id, payment_id, status=payment_status)
                                
                                # Если платеж сразу не succeeded, ждем webhook или проверяем статус
                                if payment_status != "succeeded":
                                    logger.info(f"ℹ️ Автоплатеж {payment_id} для пользователя {telegram_id} в статусе {payment_status}, ждем webhook или повторную проверку.")
                                    # Даем немного времени на обработку webhook
                                    await asyncio.sleep(3)
                                    # Проверяем статус еще раз
                                    refreshed_status = get_payment_status(payment_id)
                                    await update_payment_status(payment_id, refreshed_status)
                                    if refreshed_status != "succeeded":
                                        auto_payment_failed = True
                                        logger.warning(f"⚠️ Автоплатеж {payment_id} для пользователя {telegram_id} не завершился успешно после ожидания, статус: {refreshed_status}")
                                    else:
                                        payment_status = refreshed_status
                                        logger.info(f"✅ Автоплатеж {payment_id} для пользователя {telegram_id} успешно завершен после ожидания.")
                                
                                # Если платеж успешен (сразу или после ожидания)
                                if payment_status == "succeeded" and not auto_payment_failed:
                                    await activate_subscription_days(telegram_id, days=SUBSCRIPTION_DAYS)
                                    
                                    # Разбаниваем пользователя, если был забанен
                                    try:
                                        await bot.unban_chat_member(
                                            chat_id=CHANNEL_ID,
                                            user_id=telegram_id,
                                            only_if_banned=True
                                        )
                                    except Exception:
                                        pass
                                    
                                    # Отправляем уведомление об успешном автопродлении
                                    await bot.send_message(
                                        telegram_id,
                                        "✅ Доступ автоматически продлен!\n\n"
                                        f"С вашей карты списано {PAYMENT_AMOUNT_RUB} руб.\n"
                                        f"Доступ продлен на {SUBSCRIPTION_DAYS * 1440:.0f} минут.\n\n"
                                        "Спасибо за использование автопродления!"
                                    )
                                    logger.info(f"✅ Автопродление выполнено для пользователя {telegram_id}, payment_id: {payment_id}")
                                else:
                                    # Платеж не прошел - ОТКЛЮЧАЕМ автопродление автоматически
                                    auto_payment_failed = True
                                    logger.warning(f"⚠️ Автопродление не удалось для пользователя {telegram_id}, payment_id: {payment_id}, status: {payment_status}")
                                    
                                    # Автоматически отключаем автопродление при неудаче
                                    from db import set_auto_renewal
                                    await set_auto_renewal(telegram_id, False)
                                    logger.info(f"🔄 Автопродление автоматически отключено для пользователя {telegram_id} из-за неудачного автоплатежа")
                                    
                                    # Уведомляем пользователя об отключении автопродления (только один раз)
                                    if telegram_id not in processed_users or (datetime.utcnow() - processed_users.get(telegram_id, datetime.utcnow())).total_seconds() > 300:
                                        try:
                                            await bot.send_message(
                                                telegram_id,
                                                "⚠️ <b>Автопродление отключено</b>\n\n"
                                                "Автоматическое продление доступа было отключено из-за неудачной попытки списания средств.\n\n"
                                                "Для продления доступа нажмите кнопку 💳 Получить доступ.",
                                                parse_mode="HTML"
                                            )
                                        except Exception as e:
                                            logger.warning(f"⚠️ Ошибка отправки уведомления об отключении автопродления: {e}")
                                    
                            except Exception as auto_payment_error:
                                logger.error(f"❌ Ошибка автоматического списания для пользователя {telegram_id}: {auto_payment_error}")
                                auto_payment_failed = True
                                
                                # Автоматически отключаем автопродление при ошибке
                                from db import set_auto_renewal
                                await set_auto_renewal(telegram_id, False)
                                logger.info(f"🔄 Автопродление автоматически отключено для пользователя {telegram_id} из-за ошибки автоплатежа")
                        
                        # Если автопродление не включено или не удалось, баним и отправляем ссылку на оплату
                        if not auto_renewal_enabled or not saved_payment_method_id or auto_payment_failed:
                            logger.info(f"🚫 Автопродление не работает для пользователя {telegram_id}: auto_renewal={auto_renewal_enabled}, saved_method={bool(saved_payment_method_id)}, failed={auto_payment_failed}")
                            # Отзываем ссылку пользователя (делаем её невалидной)
                            from db import get_invite_link
                            user_invite_link = await get_invite_link(telegram_id)
                            if user_invite_link:
                                revoke_invite_link(user_invite_link)
                                logger.info(f"✅ Ссылка пользователя {telegram_id} отозвана из-за истечения подписки")
                            
                            # Баним пользователя в канале (удаляем из канала)
                            try:
                                await bot.ban_chat_member(
                                    chat_id=CHANNEL_ID,
                                    user_id=telegram_id,
                                    until_date=None  # Бан навсегда (пока не оплатит снова)
                                )
                                logger.info(f"✅ Пользователь {telegram_id} забанен в канале из-за истечения подписки")
                            except Exception as ban_error:
                                logger.warning(f"⚠️ Ошибка бана пользователя {telegram_id}: {ban_error}")
                            
                            # Если автоплатеж не удался, отправляем специальное сообщение (только один раз)
                            if auto_payment_failed:
                                # Проверяем, не отправляли ли мы уже уведомление (чтобы не спамить)
                                notification_sent_key = f"auto_payment_failed_notification_{telegram_id}"
                                notification_sent_time = processed_users.get(notification_sent_key)
                                if not notification_sent_time or (datetime.utcnow() - notification_sent_time).total_seconds() > 300:
                                    await bot.send_message(
                                        telegram_id,
                                        "⚠️ <b>Автопродление отключено</b>\n\n"
                                        "Автоматическое продление подписки было отключено из-за неудачной попытки списания средств.\n\n"
                                        "Для продления доступа нажмите кнопку 💳 Получить доступ.",
                                        parse_mode="HTML"
                                    )
                                    processed_users[notification_sent_key] = datetime.utcnow()
                                    logger.info(f"📧 Отправлено уведомление об отключении автопродления пользователю {telegram_id}")
                            else:
                                # Отправляем уведомление об истечении подписки (только один раз, больше никогда)
                                # Проверяем в БД, было ли уже отправлено уведомление
                                from db import get_subscription_expired_notified, set_subscription_expired_notified
                                
                                already_notified = await get_subscription_expired_notified(telegram_id)
                                
                                # Отправляем уведомление только если еще не отправляли
                                if not already_notified:
                                    await bot.send_message(
                                        telegram_id,
                                        "⏰ <b>Ваш доступ истек</b>\n\n"
                                        "Для продления доступа нажмите кнопку 💳 Получить доступ.",
                                        parse_mode="HTML"
                                    )
                                    # Помечаем в БД, что уведомление отправлено (навсегда)
                                    await set_subscription_expired_notified(telegram_id, True)
                                    logger.info(f"📧 Отправлено уведомление об истечении подписки пользователю {telegram_id} (один раз, сохранено в БД)")
                                else:
                                    logger.info(f"⏭️ Уведомление об истечении подписки уже было отправлено пользователю {telegram_id}, пропускаем")
                        
                        # Добавляем пользователя в processed_users с текущим временем
                        processed_users[telegram_id] = datetime.utcnow()
                        logger.info(f"✅ Пользователь {telegram_id} добавлен в processed_users")
                        
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки истекшей подписки для пользователя {telegram_id}: {e}")
            
            # Очищаем обработанных пользователей при достижении лимита (оставляем только последние N)
            if len(processed_users) > MAX_NOTIFIED_USERS_CACHE_SIZE:
                # Сортируем по времени и оставляем только последние N
                sorted_users = sorted(processed_users.items(), key=lambda x: x[1], reverse=True)
                processed_users = dict(sorted_users[:MAX_NOTIFIED_USERS_CACHE_SIZE])
                logger.info(f"🧹 Очищен processed_users, оставлено {len(processed_users)} записей")
                    
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче проверки подписок: {e}")
            await asyncio.sleep(CHECK_EXPIRED_SUBSCRIPTIONS_INTERVAL_SECONDS)


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
        logger.error(f"❌ Ошибка создания notification: {e}")
        raise HTTPException(status_code=400, detail="Bad YooKassa notification")

    payment_obj = notification.object
    payment_id = payment_obj.id
    event = notification.event
    
    # Логируем все события для отладки
    logger.info(f"📥 Получено событие от ЮKassa: {event}, payment_id: {payment_id}")

    # Обрабатываем отмененные/неудачные платежи
    if event == "payment.canceled":
        logger.info(f"🔄 Обработка canceled платежа: {payment_id}")
        try:
            payment = Payment.find_one(payment_id)
            meta = payment.metadata or {}
            tg_user_id = meta.get("telegram_user_id")
            
            logger.info(f"📋 Метаданные платежа: {meta}, tg_user_id: {tg_user_id}")
            logger.debug(f"📋 Платеж из notification: {payment_obj}")
            
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
                        
                        logger.debug(f"🔍 Причина отмены: reason={reason}, party={party}, details={cancellation_details_final}")
                        
                        # Проверяем на недостаток средств (разные варианты) - ПРИОРИТЕТ 1
                        if any(keyword in reason for keyword in ['insufficient', 'funds', 'недостаточно', 'money', 'balance']):
                            cancellation_reason = "недостаточно средств"
                            message_text = (
                                "❌ Недостаточно средств на карте\n\n"
                                "💳 Проверьте баланс карты и попробуйте еще раз пройти по ссылке на оплату.\n\n"
                                "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                            )
                        # Проверяем, отменил ли пользователь сам (выход из формы) - ПРИОРИТЕТ 2
                        elif 'user' in party:
                            cancellation_reason = "отменен пользователем (выход из формы)"
                            message_text = (
                                "❌ Платёж был отменён\n\n"
                                "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                            )
                        elif any(keyword in reason for keyword in ['canceled_by_user', 'user_canceled']):
                            cancellation_reason = "отменен пользователем (выход из формы)"
                            message_text = (
                                "❌ Платёж был отменён\n\n"
                                "Вы вышли из формы оплаты без завершения платежа.\n\n"
                                "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                            )
                        # Другие причины отмены (ошибки банка, сети и т.д.) - отправляем уведомление
                        else:
                            cancellation_reason = "отменен по другой причине"
                            message_text = (
                                "❌ Платёж был отменён\n\n"
                                "Оплата не была завершена.\n\n"
                                "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                            )
                    else:
                        # Если нет деталей, по умолчанию отправляем уведомление
                        cancellation_reason = "отменен (причина неизвестна)"
                        message_text = (
                            "❌ Платёж был отменён\n\n"
                            "Оплата не была завершена.\n\n"
                            "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                        )
                        
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка при определении причины отмены: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    # В случае ошибки отправляем стандартное уведомление
                    cancellation_reason = "отменен (ошибка определения причины)"
                    message_text = (
                        "❌ Платёж был отменён\n\n"
                        "Оплата не была завершена.\n\n"
                        "Для оплаты нажмите кнопку 💳 Оплатить подписку и перейдите по новой ссылке."
                    )
                
                # ПРОВЕРЯЕМ: есть ли у пользователя активная подписка
                has_active = await has_active_subscription(tg_user_id)
                
                if has_active:
                    # Если подписка активна - не отправляем уведомление об отмене старого платежа
                    logger.info(f"ℹ️ Платеж {payment_id} отменен, но у пользователя {tg_user_id} уже есть активная подписка - уведомление не отправлено")
                elif message_text:
                    # Уведомляем пользователя если есть текст сообщения
                    try:
                        await bot.send_message(tg_user_id, message_text)
                        logger.info(f"✅ Отправлено уведомление об отмене платежа пользователю {tg_user_id}, причина: {cancellation_reason}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки уведомления об отмене платежа пользователю {tg_user_id}: {e}")
                else:
                    # Если message_text пустое или None - отправляем стандартное уведомление
                    try:
                        await bot.send_message(
                            tg_user_id,
                            "❌ Платёж был отменён\n\n"
                            "Оплата не была завершена.\n\n"
                            "Для оплаты нажмите кнопку 💳 Получить доступ и перейдите по новой ссылке."
                        )
                        logger.info(f"✅ Отправлено стандартное уведомление об отмене платежа пользователю {tg_user_id}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки уведомления об отмене платежа пользователю {tg_user_id}: {e}")
            else:
                logger.warning(f"⚠️ Нет telegram_user_id в метаданных платежа {payment_id}")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки canceled платежа {payment_id}: {e}")
            import traceback
            traceback.print_exc()
        
        return {"ok": True, "event": "payment.canceled"}

    # Обрабатываем возвраты (refunds)
    if event == "refund.succeeded":
        logger.info(f"🔄 Обработка refund.succeeded: {payment_id}")
        try:
            # Получаем информацию о возврате
            refund_obj = notification.object
            payment_id_refund = refund_obj.payment_id if hasattr(refund_obj, 'payment_id') else None
            
            logger.info(f"📋 Информация о возврате: payment_id={payment_id_refund}")
            
            if payment_id_refund:
                # Получаем оригинальный платеж
                payment = Payment.find_one(payment_id_refund)
                meta = payment.metadata or {}
                tg_user_id = meta.get("telegram_user_id")
                
                logger.info(f"📋 Метаданные платежа: {meta}, tg_user_id: {tg_user_id}")
                
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
                        logger.warning(f"⚠️ Ошибка получения суммы возврата: {e}")
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
                        logger.info(f"✅ Подписка пользователя {tg_user_id} отменена из-за возврата")
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка отмены подписки: {e}")
                    
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
                        logger.info(f"✅ Отправлено уведомление о возврате пользователю {tg_user_id}")
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки уведомления о возврате пользователю {tg_user_id}: {e}")
                else:
                    logger.warning(f"⚠️ Нет telegram_user_id в метаданных платежа {payment_id_refund}")
            else:
                logger.warning(f"⚠️ Не удалось получить payment_id из возврата")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки refund.succeeded: {e}")
            import traceback
            traceback.print_exc()
        
        return {"ok": True, "event": "refund.succeeded"}

    # Обрабатываем успешные платежи
    if event != "payment.succeeded":
        return {"ok": True, "event": event}

    if already_processed(payment_id):
        return {"ok": True, "duplicate": True}

    # Получаем актуальный статус платежа из API
    payment = Payment.find_one(payment_id)
    current_status = payment.status
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: проверяем статус ДО активации подписки
    if current_status != "succeeded":
        logger.warning(f"⚠️ Событие payment.succeeded получено, но статус платежа {payment_id} = {current_status}, игнорируем")
        mark_processed(payment_id)
        return {"ok": True, "ignored": f"status is {current_status}, not succeeded"}

    # Дополнительная проверка: проверяем, что платеж действительно оплачен
    # Проверяем поле paid и captured
    try:
        # Проверяем поле paid (если доступно)
        if hasattr(payment, 'paid'):
            if not payment.paid:
                logger.warning(f"⚠️ Платеж {payment_id} не оплачен (paid=False), игнорируем")
                mark_processed(payment_id)
                return {"ok": True, "ignored": "payment not paid"}
        
        # Проверяем поле captured (если доступно) - должно быть True для успешного платежа
        if hasattr(payment, 'captured'):
            if not payment.captured:
                logger.warning(f"⚠️ Платеж {payment_id} не захвачен (captured=False), игнорируем")
                mark_processed(payment_id)
                return {"ok": True, "ignored": "payment not captured"}
        
        # Проверяем, что сумма платежа больше 0
        if hasattr(payment, 'amount'):
            amount_value = None
            if hasattr(payment.amount, 'value'):
                amount_value = float(payment.amount.value)
            elif isinstance(payment.amount, dict):
                amount_value = float(payment.amount.get('value', 0))
            
            if amount_value is not None and amount_value <= 0:
                logger.warning(f"⚠️ Платеж {payment_id} имеет нулевую или отрицательную сумму ({amount_value}), игнорируем")
                mark_processed(payment_id)
                return {"ok": True, "ignored": f"invalid amount: {amount_value}"}
    except Exception as e:
        logger.error(f"❌ Ошибка проверки параметров платежа: {e}")
        import traceback
        logger.debug(traceback.format_exc())

    meta = payment.metadata or {}
    tg_user_id = meta.get("telegram_user_id")

    if not tg_user_id:
        mark_processed(payment_id)
        return {"ok": True, "ignored": "no telegram_user_id"}

    tg_user_id = int(tg_user_id)
    
    # Еще раз проверяем статус перед активацией подписки (на случай если изменился)
    payment_refresh = Payment.find_one(payment_id)
    if payment_refresh.status != "succeeded":
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Статус платежа {payment_id} изменился с succeeded на {payment_refresh.status} перед активацией подписки!")
        mark_processed(payment_id)
        return {"ok": True, "ignored": f"status changed to {payment_refresh.status}"}
    
    # Финальная проверка: убеждаемся что платеж действительно успешен
    if payment_refresh.status != "succeeded":
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Финальная проверка - статус платежа {payment_id} = {payment_refresh.status}, не succeeded!")
        mark_processed(payment_id)
        return {"ok": True, "ignored": f"final check failed: {payment_refresh.status}"}

    # разрешаем пользователю вступление
    allow_user(tg_user_id)
    
    # Сохраняем payment_method_id если он есть (для автопродления)
    payment_method_id = None
    payment_method_saved = False
    
    # Проверяем наличие payment_method и его статус сохранения
    logger.info(f"🔍 Проверка payment_method для платежа {payment_id}, пользователь {tg_user_id}")
    if hasattr(payment, 'payment_method') and payment.payment_method:
        pm = payment.payment_method
        logger.info(f"📋 payment_method найден: {type(pm)}")
        
        # Проверяем, сохранен ли метод оплаты
        if hasattr(pm, 'saved'):
            payment_method_saved = bool(pm.saved)
            logger.info(f"💾 payment_method.saved = {payment_method_saved} (атрибут)")
        elif isinstance(pm, dict):
            payment_method_saved = bool(pm.get('saved', False))
            logger.info(f"💾 payment_method['saved'] = {payment_method_saved} (dict)")
        else:
            logger.warning(f"⚠️ Не удалось определить saved для payment_method: {pm}")
        
        # Получаем ID метода оплаты
        # ВАЖНО: Для автоплатежей в YooKassa payment_method.id может быть равен payment.id
        # Это нормально - YooKassa использует этот ID для последующих автоплатежей
        # Но нужно убедиться, что способ оплаты действительно сохранен (saved=True)
        if hasattr(pm, 'id'):
            payment_method_id = pm.id
            logger.info(f"🆔 payment_method.id = {payment_method_id} (атрибут)")
            # Проверяем, не равен ли он payment.id
            if payment_method_id == payment_id:
                logger.info(f"ℹ️ payment_method.id ({payment_method_id}) равен payment.id - это нормально для первого платежа в YooKassa")
            # Пробуем получить дополнительную информацию о карте
            if hasattr(pm, 'card'):
                card_info = {}
                if hasattr(pm.card, 'last4'):
                    card_info['last4'] = pm.card.last4
                if hasattr(pm.card, 'card_type'):
                    card_info['card_type'] = pm.card.card_type
                logger.info(f"💳 Информация о карте: {card_info}")
        elif isinstance(pm, dict) and 'id' in pm:
            payment_method_id = pm['id']
            logger.info(f"🆔 payment_method['id'] = {payment_method_id} (dict)")
            if payment_method_id == payment_id:
                logger.info(f"ℹ️ payment_method['id'] ({payment_method_id}) равен payment.id - это нормально для первого платежа в YooKassa")
        else:
            logger.warning(f"⚠️ Не удалось получить id для payment_method: {pm}")
    else:
        logger.warning(f"⚠️ payment_method отсутствует или None для платежа {payment_id}")
    
    # Активируем подписку (используем SUBSCRIPTION_DAYS из config)
    await activate_subscription(tg_user_id, days=SUBSCRIPTION_DAYS)
    logger.info(f"✅ Подписка активирована для пользователя {tg_user_id} на {SUBSCRIPTION_DAYS * 1440:.0f} минут")
    
    # Проверяем тип платежного метода - для QR-кода и других методов без карты не включаем автопродление
    payment_method_type = None
    if hasattr(payment, 'payment_method') and payment.payment_method:
        pm = payment.payment_method
        if hasattr(pm, 'type'):
            payment_method_type = pm.type
        elif isinstance(pm, dict) and 'type' in pm:
            payment_method_type = pm['type']
        logger.info(f"🔍 Тип платежного метода: {payment_method_type}")
    
    # Сохраняем payment_method_id и автоматически включаем автопродление
    # ВАЖНО: Автопродление включаем ТОЛЬКО если:
    # 1. payment_method_id есть
    # 2. payment_method_saved = True (пользователь явно сохранил карту)
    # 3. Тип платежного метода - банковская карта (не QR-код и не другие методы)
    if payment_method_id and payment_method_saved:
        # Проверяем, что это банковская карта (не QR-код)
        if payment_method_type and payment_method_type.lower() not in ['bank_card', 'card']:
            logger.warning(f"⚠️ Тип платежного метода {payment_method_type} не поддерживает автопродление (только банковские карты)")
            payment_method_id = None  # Не сохраняем для не-карт
        else:
            from db import save_payment_method, set_auto_renewal
            await save_payment_method(tg_user_id, payment_method_id)
            logger.info(f"💾 Сохранен payment_method_id для пользователя {tg_user_id}: {payment_method_id}")
            
            # Включаем автопродление только если карта сохранена
            await set_auto_renewal(tg_user_id, True)
            logger.info(f"✅ Автопродление автоматически включено для пользователя {tg_user_id} (saved=True)")
            
            # Уведомляем пользователя о сохранении карты и включении автопродления
            try:
                await bot.send_message(
                    tg_user_id,
                    "💳 <b>Карта сохранена для автопродления</b>\n\n"
                    f"✅ Ваша карта сохранена и будет использоваться для автоматического продления доступа.\n\n"
                    f"🔄 Доступ будет автоматически продлеваться каждые {SUBSCRIPTION_DAYS * 1440:.0f} минут.\n\n"
                    "⚙️ Вы можете отключить автопродление в меню «Управление доступом».",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"⚠️ Ошибка отправки уведомления о сохранении карты: {e}")
    else:
        if not payment_method_id:
            logger.info(f"ℹ️ Платеж {payment_id}: payment_method_id отсутствует - автопродление НЕ будет включено")
        elif not payment_method_saved:
            logger.info(f"ℹ️ Платеж {payment_id}: payment_method не сохранен пользователем (saved=False) - автопродление НЕ будет включено")
        elif payment_method_type and payment_method_type.lower() not in ['bank_card', 'card']:
            logger.info(f"ℹ️ Платеж {payment_id}: тип платежного метода {payment_method_type} не поддерживает автопродление")
    
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

    # Получаем дату окончания подписки для установки expire_date ссылки
    from db import get_subscription_expires_at
    subscription_expires_at = await get_subscription_expires_at(tg_user_id)
    
    # Создаем ПРИГЛАСИТЕЛЬНУЮ ссылку (прямой доступ) - пользователь заплатил!
    # Ссылка будет одноразовой (member_limit=1) и действительна до окончания подписки
    invite_link = None
    try:
        # Используем expires_at подписки как expire_date ссылки
        # Если подписка истекает через 30 дней, ссылка будет валидна 30 дней
        link_expire_date = subscription_expires_at if subscription_expires_at else (datetime.utcnow() + timedelta(days=SUBSCRIPTION_DAYS))
        
        # Сначала пробуем создать ссылку БЕЗ заявки (если канал поддерживает)
        try:
            invite = await bot.create_chat_invite_link(
                chat_id=CHANNEL_ID,
                creates_join_request=False,  # БЕЗ заявки - прямой доступ
                member_limit=1,  # Одноразовая ссылка - только один пользователь может использовать
                expire_date=link_expire_date  # Ссылка действительна до окончания подписки
            )
            invite_link = invite.invite_link
            logger.info(f"✅ Создана одноразовая ссылка (без заявки) для пользователя {tg_user_id}, действительна до {link_expire_date}")
        except Exception as e1:
            # Если не получилось, пробуем без параметра creates_join_request (по умолчанию)
            logger.warning(f"⚠️ Первая попытка создания ссылки не удалась: {e1}, пробуем второй вариант")
            try:
                invite = await bot.create_chat_invite_link(
                    chat_id=CHANNEL_ID,
                    member_limit=1,  # Одноразовая ссылка
                    expire_date=link_expire_date  # Ссылка действительна до окончания подписки
                )
                invite_link = invite.invite_link
                logger.info(f"✅ Создана одноразовая ссылка (второй вариант) для пользователя {tg_user_id}, действительна до {link_expire_date}")
            except Exception as e2:
                # Если и это не получилось, пробуем основную ссылку канала
                logger.warning(f"⚠️ Вторая попытка не удалась: {e2}, пробуем основную ссылку канала")
                try:
                    chat = await bot.get_chat(CHANNEL_ID)
                    if chat.invite_link:
                        invite_link = chat.invite_link
                        logger.info(f"✅ Используется основная ссылка канала для пользователя {tg_user_id}")
                    else:
                        raise Exception("У канала нет основной ссылки")
                except Exception as e3:
                    logger.error(f"❌ Все попытки создания ссылки не удались: {e3}")
                    raise e3
    except Exception as e:
        logger.error(f"❌ Ошибка создания пригласительной ссылки: {e}")
        import traceback
        traceback.print_exc()
        # Отправляем сообщение об ошибке
        # ВАЖНО: Принудительно обновляем меню после оплаты, чтобы показать правильные кнопки
        menu = await get_main_menu_for_user(tg_user_id)
        
        await bot.send_message(
            tg_user_id,
            "✅ <b>Оплата подтверждена!</b>\n\n"
            "Произошла ошибка при создании ссылки. Пожалуйста, свяжитесь с администратором.",
            parse_mode="HTML",
            reply_markup=menu
        )
        mark_processed(payment_id)
        return {"ok": True, "error": "failed to create invite link"}

    # Получаем даты начала и окончания подписки (уже сохранены выше)
    from db import get_subscription_expires_at, get_subscription_starts_at
    expires_at_dt = await get_subscription_expires_at(tg_user_id)
    starts_at_dt = await get_subscription_starts_at(tg_user_id)
    
    # Сохраняем информацию о ссылке в БД и отправляем сообщение
    # ВАЖНО: Ссылка отправляется только если она была успешно создана
    if invite_link:
        save_invite_link(invite_link, tg_user_id, payment_id)
        
        # Форматируем даты для отображения
        if starts_at_dt and expires_at_dt:
            starts_str = format_datetime_moscow(starts_at_dt)
            expires_str = format_datetime_moscow(expires_at_dt)
        else:
            # Если даты не найдены, используем текущее время (запасной вариант)
            starts_at_dt = datetime.utcnow()
            expires_at_dt = starts_at_dt + timedelta(days=SUBSCRIPTION_DAYS)
            starts_str = format_datetime_moscow(starts_at_dt)
            expires_str = format_datetime_moscow(expires_at_dt)

        # Получаем меню с обновленными кнопками (теперь должна быть "Управление доступом")
        # ВАЖНО: Принудительно обновляем меню после оплаты, чтобы показать правильные кнопки
        menu = await get_main_menu_for_user(tg_user_id)
        
        # Вычисляем длительность доступа в минутах для отображения
        duration_minutes = SUBSCRIPTION_DAYS * 1440
        
        await bot.send_message(
            tg_user_id,
            "✅ <b>Оплата подтверждена!</b>\n\n"
            f"📅 <b>Доступ активен с:</b> {starts_str}\n"
            f"📅 <b>Доступ активен до:</b> {expires_str}\n\n"
            f"⏱️ <b>Длительность доступа:</b> {duration_minutes:.0f} минут\n"
            f"🔄 <b>Автопродление:</b> каждые {duration_minutes:.0f} минут\n\n"
            "Нажмите на ссылку ниже, чтобы попасть в канал:\n"
            f"{invite_link}",
            parse_mode="HTML",
            reply_markup=menu
        )
    else:
        # Если ссылка не создана, отправляем сообщение без ссылки
        logger.warning(f"⚠️ Ссылка на канал не была создана для пользователя {tg_user_id}, отправляем сообщение без ссылки")
        
        # Форматируем даты для отображения
        if starts_at_dt and expires_at_dt:
            starts_str = format_datetime_moscow(starts_at_dt)
            expires_str = format_datetime_moscow(expires_at_dt)
        else:
            starts_at_dt = datetime.utcnow()
            expires_at_dt = starts_at_dt + timedelta(days=SUBSCRIPTION_DAYS)
            starts_str = format_datetime_moscow(starts_at_dt)
            expires_str = format_datetime_moscow(expires_at_dt)
        
        # ВАЖНО: Принудительно обновляем меню после оплаты, чтобы показать правильные кнопки
        menu = await get_main_menu_for_user(tg_user_id)
        
        # Вычисляем длительность доступа в минутах для отображения
        duration_minutes = SUBSCRIPTION_DAYS * 1440
        
        await bot.send_message(
            tg_user_id,
            "✅ <b>Оплата подтверждена!</b>\n\n"
            f"📅 <b>Доступ активен с:</b> {starts_str}\n"
            f"📅 <b>Доступ активен до:</b> {expires_str}\n\n"
            f"⏱️ <b>Длительность доступа:</b> {duration_minutes:.0f} минут\n"
            f"🔄 <b>Автопродление:</b> каждые {duration_minutes:.0f} минут\n\n"
            "Для получения доступа к каналу используйте кнопку 📊 Статус доступа.",
            parse_mode="HTML",
            reply_markup=menu
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
                if await is_user_allowed(user_id) and chat_id == CHANNEL_ID:
                    try:
                        await bot.approve_chat_join_request(
                            chat_id=chat_id,
                            user_id=user_id
                        )
                        return {"ok": True, "approved": True}
                    except Exception as e:
                        # Логируем ошибку, но не падаем
                        logger.error(f"Error approving join request: {e}")
                        return {"ok": True, "approved": False, "error": str(e)}
                else:
                    # Пользователь не оплатил или это не наш канал
                    return {"ok": True, "approved": False}
        except Exception as e:
            logger.error(f"Error processing chat_join_request: {e}")
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
        if await is_user_allowed(user_id) and (not chat_id or int(chat_id) == CHANNEL_ID):
            try:
                await bot.approve_chat_join_request(
                    chat_id=chat_id or CHANNEL_ID,
                    user_id=user_id
                )
                return {"ok": True, "approved": True}
            except Exception as e:
                logger.error(f"Error approving join request: {e}")
                return {"ok": True, "approved": False, "error": str(e)}

        return {"ok": True, "approved": False}
    except Exception as e:
        logger.error(f"Error in join_request handler: {e}")
        return {"ok": True, "error": str(e)}

