import os
import logging
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Токен из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8525784017:AAGyonwOxkChbavfqMhT1e4IFLa89mgt_Ys')

class DailyMessageBot:
    def __init__(self):
        self.token = BOT_TOKEN
        self.silent_mode = False
        self.silent_start_time = "22:00"
        self.silent_end_time = "08:00"
        self.welcome_time = "09:00"
        self.cleanup_time = "18:00"
        self.welcome_mode = True
        self.daily_messages = {}
        self.topics_to_clean = []
        self.scheduler = BackgroundScheduler(timezone=pytz.UTC)
        self.setup_schedulers()
        
    def setup_schedulers(self):
        """Настройка планировщиков"""
        if self.welcome_mode and self.daily_messages:
            self.schedule_welcome_message()
        self.schedule_cleanup()
        self.scheduler.start()

    def schedule_welcome_message(self):
        """Планировщик для приветственных сообщений"""
        try:
            welcome_hour, welcome_minute = map(int, self.welcome_time.split(':'))
            self.scheduler.add_job(
                self.send_welcome_message_job,
                CronTrigger(hour=welcome_hour, minute=welcome_minute),
                id='welcome_message'
            )
        except Exception as e:
            logging.error(f"Ошибка планировщика приветствий: {e}")

    def schedule_cleanup(self):
        """Планировщик для очистки тем"""
        try:
            cleanup_hour, cleanup_minute = map(int, self.cleanup_time.split(':'))
            self.scheduler.add_job(
                self.cleanup_messages_job,
                CronTrigger(hour=cleanup_hour, minute=cleanup_minute),
                id='cleanup'
            )
        except Exception as e:
            logging.error(f"Ошибка планировщика очистки: {e}")

    async def send_welcome_message_job(self):
        """Задача для отправки приветственного сообщения"""
        logging.info("✅ Запуск отправки приветственного сообщения")

    async def cleanup_messages_job(self):
        """Задача для очистки сообщений в темах"""
        logging.info("✅ Запуск очистки сообщений в темах")

    def is_silent_time(self):
        """Проверка, сейчас время режима тишины"""
        if not self.silent_mode:
            return False
        
        now = datetime.now().time()
        start_time = datetime.strptime(self.silent_start_time, "%H:%M").time()
        end_time = datetime.strptime(self.silent_end_time, "%H:%M").time()
        
        if start_time < end_time:
            return start_time <= now <= end_time
        else:
            return now >= start_time or now <= end_time

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда старт"""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени", callback_data="timers")],
            [InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text("👋 Главное меню:", reply_markup=reply_markup)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "modes":
            await self.show_modes_menu(query)
        elif data == "timers":
            await self.show_timers_menu(query)
        elif data == "status":
            await self.show_status(query)
        elif data.startswith("mode_"):
            await self.handle_mode_change(query, data)
        elif data.startswith("timer_"):
            await self.handle_timer_change(query, data)
        elif data == "back_main":
            await self.show_main_menu(query)

    async def show_main_menu(self, query):
        """Главное меню"""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени", callback_data="timers")],
            [InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("👋 Главное меню:", reply_markup=reply_markup)

    async def show_timers_menu(self, query):
        """Меню времени"""
        keyboard = [
            [InlineKeyboardButton(f"🕐 Приветствие: {self.welcome_time}", callback_data="timer_welcome")],
            [InlineKeyboardButton(f"🔇 Начало тишины: {self.silent_start_time}", callback_data="timer_silent_start")],
            [InlineKeyboardButton(f"🔊 Конец тишины: {self.silent_end_time}", callback_data="timer_silent_end")],
            [InlineKeyboardButton(f"🗑️ Очистка тем: {self.cleanup_time}", callback_data="timer_cleanup")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "⏰ Настройка времени:\n\nФормат: ЧЧ:ММ (например: 22:30)",
            reply_markup=reply_markup
        )

    async def show_modes_menu(self, query):
        """Меню режимов"""
        silent_status = "🔇 ВКЛ" if self.silent_mode else "🔊 ВЫКЛ"
        welcome_status = "👋 ВКЛ" if self.welcome_mode else "🚫 ВЫКЛ"
        
        keyboard = [
            [InlineKeyboardButton(f"Режим тишины: {silent_status}", callback_data="mode_silent")],
            [InlineKeyboardButton(f"Приветствия: {welcome_status}", callback_data="mode_welcome")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text("⚙️ Управление режимами:", reply_markup=reply_markup)

    async def handle_timer_change(self, query, data):
        """Обработка изменения времени"""
        if data == "timer_welcome":
            await query.edit_message_text(f"Введите время приветствия:\nСейчас: {self.welcome_time}\n\nПример: 09:00")
            return "WAITING_WELCOME_TIME"
        elif data == "timer_silent_start":
            await query.edit_message_text(f"Введите начало тишины:\nСейчас: {self.silent_start_time}\n\nПример: 22:00")
            return "WAITING_SILENT_START"
        elif data == "timer_silent_end":
            await query.edit_message_text(f"Введите конец тишины:\nСейчас: {self.silent_end_time}\n\nПример: 08:00")
            return "WAITING_SILENT_END"
        elif data == "timer_cleanup":
            await query.edit_message_text(f"Введите время очистки:\nСейчас: {self.cleanup_time}\n\nПример: 18:00")
            return "WAITING_CLEANUP_TIME"

    async def handle_mode_change(self, query, data):
        """Обработка изменения режимов"""
        if data == "mode_silent":
            self.silent_mode = not self.silent_mode
            status = "включен" if self.silent_mode else "выключен"
            await query.edit_message_text(f"✅ Режим тишины {status}!")
            await self.show_modes_menu(query)
        elif data == "mode_welcome":
            self.welcome_mode = not self.welcome_mode
            status = "включен" if self.welcome_mode else "выключен"
            await query.edit_message_text(f"✅ Приветствия {status}!")
            await self.show_modes_menu(query)

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        user_data = context.user_data
        text = update.message.text
        
        # Режим тишины
        if self.is_silent_time():
            try:
                await update.message.delete()
            except:
                pass
            return
        
        # Отмена
        if text.lower() == "/cancel":
            await update.message.reply_text("❌ Отменено")
            await self.start(update, context)
            return
        
        # Обработка времени
        if 'waiting_welcome_time' in user_data:
            if self.validate_time(text):
                self.welcome_time = text
                self.schedule_welcome_message()
                await update.message.reply_text(f"✅ Приветствие: {text}")
                await self.show_timers_menu_from_message(update, context)
            return
        
        elif 'waiting_silent_start' in user_data:
            if self.validate_time(text):
                self.silent_start_time = text
                await update.message.reply_text(f"✅ Начало тишины: {text}")
                await self.show_timers_menu_from_message(update, context)
            return
        
        elif 'waiting_silent_end' in user_data:
            if self.validate_time(text):
                self.silent_end_time = text
                await update.message.reply_text(f"✅ Конец тишины: {text}")
                await self.show_timers_menu_from_message(update, context)
            return
        
        elif 'waiting_cleanup_time' in user_data:
            if self.validate_time(text):
                self.cleanup_time = text
                self.schedule_cleanup()
                await update.message.reply_text(f"✅ Очистка: {text}")
                await self.show_timers_menu_from_message(update, context)
            return

    def validate_time(self, time_str):
        """Проверка формата времени"""
        try:
            datetime.strptime(time_str, "%H:%M")
            return True
        except:
            return False

    async def show_timers_menu_from_message(self, update, context):
        """Показать меню времени"""
        keyboard = [
            [InlineKeyboardButton(f"🕐 Приветствие: {self.welcome_time}", callback_data="timer_welcome")],
            [InlineKeyboardButton(f"🔇 Начало тишины: {self.silent_start_time}", callback_data="timer_silent_start")],
            [InlineKeyboardButton(f"🔊 Конец тишины: {self.silent_end_time}", callback_data="timer_silent_end")],
            [InlineKeyboardButton(f"🗑️ Очистка тем: {self.cleanup_time}", callback_data="timer_cleanup")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("⏰ Настройка времени:", reply_markup=reply_markup)

    async def show_status(self, query):
        """Показать статус"""
        silent_status = "🔇 ВКЛ" if self.silent_mode else "🔊 ВЫКЛ"
        text = f"📊 Статус:\n\n🔇 Тишина: {silent_status}\n🕐 Время: {self.silent_start_time} - {self.silent_end_time}"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup)

def main():
    """Запуск бота"""
    bot = DailyMessageBot()
    application = Application.builder().token(bot.token).build()
    
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("cancel", bot.start))
    application.add_handler(CallbackQueryHandler(bot.button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text_message))
    
    print("✅ Бот запущен на Railway!")
    application.run_polling()

if __name__ == "__main__":
    main()