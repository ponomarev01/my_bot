import os
import logging
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Токен бота
BOT_TOKEN = "8525784017:AAGyonwOxkChbavfqMhT1e4IFLa89mgt_Ys"

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
            logging.info(f"✅ Приветствие запланировано на {self.welcome_time}")
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
            logging.info(f"✅ Очистка запланирована на {self.cleanup_time}")
        except Exception as e:
            logging.error(f"Ошибка планировщика очистки: {e}")

    def send_welcome_message_job(self):
        """Задача для отправки приветственного сообщения"""
        logging.info("✅ Запуск отправки приветственного сообщения")

    def cleanup_messages_job(self):
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

    def start(self, update, context):
        """Команда старт"""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени", callback_data="timers")],
            [InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text("👋 Главное меню:", reply_markup=reply_markup)

    def button_handler(self, update, context):
        """Обработчик кнопок"""
        query = update.callback_query
        query.answer()
        
        data = query.data
        
        if data == "modes":
            self.show_modes_menu(query)
        elif data == "timers":
            self.show_timers_menu(query)
        elif data == "status":
            self.show_status(query)
        elif data.startswith("mode_"):
            self.handle_mode_change(query, data)
        elif data.startswith("timer_"):
            self.handle_timer_change(query, data)
        elif data == "back_main":
            self.show_main_menu(query)
        elif data == "back_timers":
            self.show_timers_menu(query)

    def show_main_menu(self, query):
        """Главное меню"""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени", callback_data="timers")],
            [InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text("👋 Главное меню:", reply_markup=reply_markup)

    def show_timers_menu(self, query):
        """Меню времени"""
        keyboard = [
            [InlineKeyboardButton(f"🕐 Приветствие: {self.welcome_time}", callback_data="timer_welcome")],
            [InlineKeyboardButton(f"🔇 Начало тишины: {self.silent_start_time}", callback_data="timer_silent_start")],
            [InlineKeyboardButton(f"🔊 Конец тишины: {self.silent_end_time}", callback_data="timer_silent_end")],
            [InlineKeyboardButton(f"🗑️ Очистка тем: {self.cleanup_time}", callback_data="timer_cleanup")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            "⏰ Настройка времени:\n\nФормат: ЧЧ:ММ (например: 22:30)",
            reply_markup=reply_markup
        )

    def show_modes_menu(self, query):
        """Меню режимов"""
        silent_status = "🔇 ВКЛ" if self.silent_mode else "🔊 ВЫКЛ"
        welcome_status = "👋 ВКЛ" if self.welcome_mode else "🚫 ВЫКЛ"
        
        keyboard = [
            [InlineKeyboardButton(f"Режим тишины: {silent_status}", callback_data="mode_silent")],
            [InlineKeyboardButton(f"Приветствия: {welcome_status}", callback_data="mode_welcome")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text("⚙️ Управление режимами:", reply_markup=reply_markup)

    def handle_timer_change(self, query, data):
        """Обработка изменения времени"""
        if data == "timer_welcome":
            query.edit_message_text(f"⏰ Введите время приветствия:\nСейчас: {self.welcome_time}\n\nПример: 09:00\n\n❌ Отмена - /cancel")
            context.user_data['waiting_welcome_time'] = True
        elif data == "timer_silent_start":
            query.edit_message_text(f"🔇 Введите начало тишины:\nСейчас: {self.silent_start_time}\n\nПример: 22:00\n\n❌ Отмена - /cancel")
            context.user_data['waiting_silent_start'] = True
        elif data == "timer_silent_end":
            query.edit_message_text(f"🔊 Введите конец тишины:\nСейчас: {self.silent_end_time}\n\nПример: 08:00\n\n❌ Отмена - /cancel")
            context.user_data['waiting_silent_end'] = True
        elif data == "timer_cleanup":
            query.edit_message_text(f"🗑️ Введите время очистки:\nСейчас: {self.cleanup_time}\n\nПример: 18:00\n\n❌ Отмена - /cancel")
            context.user_data['waiting_cleanup_time'] = True

    def handle_mode_change(self, query, data):
        """Обработка изменения режимов"""
        if data == "mode_silent":
            self.silent_mode = not self.silent_mode
            status = "включен" if self.silent_mode else "выключен"
            query.edit_message_text(f"✅ Режим тишины {status}!")
            self.show_modes_menu(query)
        elif data == "mode_welcome":
            self.welcome_mode = not self.welcome_mode
            status = "включен" if self.welcome_mode else "выключен"
            query.edit_message_text(f"✅ Приветствия {status}!")
            self.show_modes_menu(query)

    def handle_text_message(self, update, context):
        """Обработка текстовых сообщений"""
        user_data = context.user_data
        text = update.message.text
        
        # Режим тишины - ТИХОЕ удаление
        if self.is_silent_time():
            try:
                update.message.delete()
                logging.info("✅ Сообщение удалено в режиме тишины")
            except Exception as e:
                logging.error(f"Ошибка удаления: {e}")
            return
        
        # Отмена команды
        if text.lower() == "/cancel":
            update.message.reply_text("❌ Действие отменено")
            self.start(update, context)
            return
        
        # Обработка ввода времени
        if user_data.get('waiting_welcome_time'):
            if self.validate_time(text):
                self.welcome_time = text
                self.schedule_welcome_message()
                update.message.reply_text(f"✅ Время приветствия: {text}")
                self.show_timers_menu_from_message(update, context)
                user_data.pop('waiting_welcome_time', None)
            else:
                update.message.reply_text("❌ Неверный формат! Используйте ЧЧ:ММ\nПример: 09:30")
            return
        
        elif user_data.get('waiting_silent_start'):
            if self.validate_time(text):
                self.silent_start_time = text
                update.message.reply_text(f"✅ Начало тишины: {text}")
                self.show_timers_menu_from_message(update, context)
                user_data.pop('waiting_silent_start', None)
            else:
                update.message.reply_text("❌ Неверный формат! Используйте ЧЧ:ММ\nПример: 22:30")
            return
        
        elif user_data.get('waiting_silent_end'):
            if self.validate_time(text):
                self.silent_end_time = text
                update.message.reply_text(f"✅ Конец тишины: {text}")
                self.show_timers_menu_from_message(update, context)
                user_data.pop('waiting_silent_end', None)
            else:
                update.message.reply_text("❌ Неверный формат! Используйте ЧЧ:ММ\nПример: 08:15")
            return
        
        elif user_data.get('waiting_cleanup_time'):
            if self.validate_time(text):
                self.cleanup_time = text
                self.schedule_cleanup()
                update.message.reply_text(f"✅ Очистка тем: {text}")
                self.show_timers_menu_from_message(update, context)
                user_data.pop('waiting_cleanup_time', None)
            else:
                update.message.reply_text("❌ Неверный формат! Используйте ЧЧ:ММ\nПример: 18:30")
            return

        # Если не обработано - показываем главное меню
        self.start(update, context)

    def validate_time(self, time_str):
        """Проверка формата времени"""
        try:
            datetime.strptime(time_str, "%H:%M")
            return True
        except:
            return False

    def show_timers_menu_from_message(self, update, context):
        """Показать меню времени"""
        keyboard = [
            [InlineKeyboardButton(f"🕐 Приветствие: {self.welcome_time}", callback_data="timer_welcome")],
            [InlineKeyboardButton(f"🔇 Начало тишины: {self.silent_start_time}", callback_data="timer_silent_start")],
            [InlineKeyboardButton(f"🔊 Конец тишины: {self.silent_end_time}", callback_data="timer_silent_end")],
            [InlineKeyboardButton(f"🗑️ Очистка тем: {self.cleanup_time}", callback_data="timer_cleanup")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text("⏰ Настройка времени:", reply_markup=reply_markup)

    def show_status(self, query):
        """Показать статус"""
        silent_status = "🔇 ВКЛ" if self.silent_mode else "🔊 ВЫКЛ"
        silent_active = "✅ АКТИВЕН" if self.is_silent_time() else "❌ НЕАКТИВЕН"
        
        text = (
            f"📊 Статус бота:\n\n"
            f"🔇 Режим тишины: {silent_status}\n"
            f"🕐 Статус тишины: {silent_active}\n"
            f"⏰ Время тишины: {self.silent_start_time} - {self.silent_end_time}\n"
            f"👋 Приветствия: {'ВКЛ' if self.welcome_mode else 'ВЫКЛ'}\n"
            f"🕐 Время приветствия: {self.welcome_time}\n"
            f"🗑️ Очистка тем: {self.cleanup_time}\n\n"
            f"💡 Режим тишины работает ТИХО"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(text, reply_markup=reply_markup)

def main():
    """Запуск бота"""
    bot = DailyMessageBot()
    
    # Создаем updater для версии 13.15
    updater = Updater(token=bot.token, use_context=True)
    
    # Регистрация обработчиков
    updater.dispatcher.add_handler(CommandHandler("start", bot.start))
    updater.dispatcher.add_handler(CommandHandler("cancel", bot.start))
    updater.dispatcher.add_handler(CallbackQueryHandler(bot.button_handler))
    updater.dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, bot.handle_text_message))
    
    print("✅ Бот запущен на Render!")
    print("⏰ Время приветствия:", bot.welcome_time)
    print("🔇 Тишина:", bot.silent_start_time, "-", bot.silent_end_time)
    print("🗑️ Очистка:", bot.cleanup_time)
    
    # Запуск бота
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
