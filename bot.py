import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()


def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Оплатить доступ")],
            [KeyboardButton(text="ℹ️ О проекте")]
        ],
        resize_keyboard=True
    )


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Это тестовый бот для MVP.\nВыбери действие:",
        reply_markup=main_menu()
    )


@dp.message(lambda message: message.text == "ℹ️ О проекте")
async def about(message: Message):
    await message.answer(
        "Это MVP Telegram-бота с доступом в закрытый канал.\n"
        "Оплата и подписка будут добавлены дальше."
    )


@dp.message(lambda message: message.text == "💳 Оплатить доступ")
async def pay_stub(message: Message):
    await message.answer(
        "💳 Оплата будет подключена на следующем этапе."
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
