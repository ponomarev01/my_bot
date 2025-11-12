import asyncio
import json
import logging
from datetime import datetime, time
from pytz import timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# ------------------------------------------------------------
# НАСТРОЙКИ
# ------------------------------------------------------------

BOT_TOKEN = "YOUR_TOKEN_HERE"  # ← вставь свой токен
DATA_FILE = "bot_data.json"
MOSCOW_TZ = timezone("Europe/Moscow")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ------------------------------------------------------------
# КЛАСС БОТА
# ------------------------------------------------------------

class DailyMessageBot:
    def __init__(self, application):
        self.app = application
        self.scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
        self.data = self.load_data()

        # Команды
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CallbackQueryHandler(self.handle_button))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))

    # ------------------ JSON ХРАНИЛКА -------------------
    def load_data(self):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {"greetings": {}, "autoreplies": {}, "autodelete": {}, "silent": {}}

    def save_data(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    # ------------------ START -------------------
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("👋 Приветствия", callback_data="menu_greetings")],
            [InlineKeyboardButton("🧹 Автоочистка", callback_data="menu_autodelete")],
            [InlineKeyboardButton("💬 Автоответы", callback_data="menu_autoreply")],
            [InlineKeyboardButton("🔇 Тихий режим", callback_data="menu_silent")],
            [InlineKeyboardButton("📊 Статус", callback_data="menu_status")],
        ]
        await update.message.reply_text(
            "Главное меню управления ботом 👇",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    # ------------------ CALLBACK HANDLER -------------------
    async def handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        await query.answer()

        if data == "menu_greetings":
            await self.show_greetings_menu(query)
        elif data == "menu_autodelete":
            await self.show_autodelete_menu(query)
        elif data == "menu_autoreply":
            await self.show_autoreply_menu(query)
        elif data == "menu_silent":
            await self.show_silent_menu(query)
        elif data == "menu_status":
            await self.show_status(query)
        elif data == "back_main":
            await self.start(update=Update.de_json(query.to_dict(), self.app), context=context)

    # ------------------ ТЕКСТОВЫЕ СООБЩЕНИЯ -------------------
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_text = update.message.text.lower()
        for key, reply in self.data.get("autoreplies", {}).items():
            if key.lower() in user_text:
                await update.message.reply_text(reply)
                return

    # ------------------ МЕНЮ -------------------
    async def show_greetings_menu(self, query):
        text = "Настройка ежедневных приветствий (Europe/Moscow)"
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_autodelete_menu(self, query):
        text = "Настройка автоочистки чата"
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_autoreply_menu(self, query):
        text = "Настройка автоответов"
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_silent_menu(self, query):
        text = "Настройка режима тишины (время, когда бот удаляет всё)"
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    async def show_status(self, query):
        g_count = len(self.data.get("greetings", {}))
        a_count = len(self.data.get("autoreplies", {}))
        d_count = len(self.data.get("autodelete", {}))
        silent = "✅ Включён" if self.data.get("silent", {}).get("enabled") else "❌ Выключен"
        text = (
            f"📊 Статус бота:\n"
            f"👋 Приветствия: {g_count}\n"
            f"💬 Автоответы: {a_count}\n"
            f"🧹 Автоочистка: {d_count}\n"
            f"🔇 Тихий режим: {silent}"
        )
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    # ------------------ ПЛАНИРОВЩИК -------------------
    def schedule_greetings(self):
        """Пример: каждое утро в 9:00 по Москве"""
        self.scheduler.add_job(
            self.send_daily_greeting,
            CronTrigger(hour=9, minute=0, timezone=MOSCOW_TZ),
        )

    async def send_daily_greeting(self):
        # Здесь ты можешь подставить ID темы/чата и текст
        chat_id = self.data.get("greetings_chat_id")
        text = self.data.get("greetings", {}).get("default", "Доброе утро!")
        if chat_id:
            try:
                await self.app.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                logging.error(f"Ошибка при отправке приветствия: {e}")

    # ------------------ СТАРТ -------------------
    async def run(self):
        self.schedule_greetings()
        self.scheduler.start()
        await self.app.run_polling()


# ------------------------------------------------------------
# ОСНОВНАЯ ФУНКЦИЯ
# ------------------------------------------------------------

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    bot = DailyMessageBot(app)
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен.")
