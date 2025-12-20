import os
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from yookassa.domain.notification import WebhookNotificationFactory

load_dotenv()

app = FastAPI()

@app.post("/yookassa/webhook")
async def yookassa_webhook(request: Request):
    body = await request.body()

    try:
        notification = WebhookNotificationFactory().create(body.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad notification: {e}")

    event = notification.event
    obj = notification.object  # payment/refund entity

    # важно: нас интересует успешная оплата
    if event == "payment.succeeded":
        # metadata ты должен был положить при Payment.create()
        meta = getattr(obj, "metadata", {}) or {}
        telegram_user_id = meta.get("telegram_user_id")
        tariff = meta.get("tariff", "default")

        if not telegram_user_id:
            # без привязки к пользователю мы не знаем кому выдавать доступ
            return {"ok": True, "ignored": "no telegram_user_id in metadata"}

        # TODO: тут твоя логика:
        # 1) записать подписку в БД (paid_at, expires_at, payment_id)
        # 2) выдать доступ в канал (createChatInviteLink / invite link)
        # 3) отправить сообщение пользователю

        return {"ok": True}

    return {"ok": True}
