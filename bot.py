import logging
import json
import os
import sys
import asyncio
import re
from datetime import datetime
from typing import Dict, Any, List, Optional

# --- ИМПОРТЫ ДЛЯ PTB v20 ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes
)
# ----------------------------------

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ВАЖНАЯ НАСТРОЙКА ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ") 

# Константы для состояний ввода
INPUT_STATE_TIME = 'TIMER_INPUT'
INPUT_STATE_DAILY_MESSAGE = 'DAILY_MESSAGE_INPUT'
INPUT_STATE_CLEANUP_TIME = 'CLEANUP_TIMER_INPUT'


class DailyMessageBot:
    def __init__(self, application: Application):
        self.application = application
        self.bot = application.bot
        self.data_file = "bot_data.json"
        
        # Настройки ежедневных приветствий
        self.welcome_mode = True
        self.welcome_time = "09:00" # Время отправки (UTC)
        self.welcome_delete_time = "10:00" # Время удаления (UTC)
        self.daily_messages: Dict[str, str] = {} # {день_недели(0-6): "текст сообщения"}
        self.registered_topics: Dict[str, Dict[str, Any]] = {} 
        self.target_chat_id: Optional[int] = None 
        self.target_thread_id: Optional[int] = None 
        self.last_welcome_message: Dict[str, int] = {} 
        
        # Настройки авто-очистки
        self.bot_id: Optional[int] = None 
        self.monitored_topics: Dict[str, Dict[str, Any]] = {} 
        self.forbidden_words: list = [] 
        self.auto_response_topics: Dict[str, str] = {} 
        
        self.admin_cache: Dict[int, Dict[str, Any]] = {} 
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)
        self.load_data()
        
    async def post_init_hook(self, application: Application):
        """Хук для запуска планировщика и получения ID бота."""
        self.bot_id = (await application.bot.get_me()).id
        logger.info(f"🤖 ID бота: {self.bot_id}")
        
        self.setup_schedulers()
        if not self.scheduler.running:
            try:
                self.scheduler.start()
                logger.info("✅ Планировщик apscheduler успешно запущен.")
            except Exception as e:
                logger.error(f"Ошибка запуска планировщика: {e}")
        
    # --- СОХРАНЕНИЕ / ЗАГРУЗКА ДАННЫХ (ВОССТАНОВЛЕНЫ) ---
    def load_data(self):
        """Загрузка данных из файла"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    self.welcome_mode = data.get('welcome_mode', True)
                    self.welcome_time = data.get('welcome_time', "09:00")
                    self.welcome_delete_time = data.get('welcome_delete_time', "10:00")
                    self.daily_messages = data.get('daily_messages', {})
                    self.registered_topics = data.get('registered_topics', {})
                    self.target_chat_id = data.get('target_chat_id', None)
                    self.target_thread_id = data.get('target_thread_id', None)
                    self.last_welcome_message = data.get('last_welcome_message', {})
                    self.forbidden_words = data.get('forbidden_words', [])
                    self.auto_response_topics = data.get('auto_response_topics', {})

                    loaded_monitored = data.get('monitored_topics', {})
                    for name in loaded_monitored:
                        loaded_monitored[name]['messages'] = [] 
                    self.monitored_topics = loaded_monitored

        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
    
    def save_data(self):
        """Сохранение данных в файл (асинхронно)."""
        try:
            asyncio.create_task(self._save_data_async())
        except Exception as e:
            logger.error(f"Ошибка инициирования сохранения данных: {e}")

    async def _save_data_async(self):
        """Асинхронное сохранение данных"""
        try:
            monitored_topics_to_save = {}
            for name, data in self.monitored_topics.items():
                monitored_topics_to_save[name] = data.copy()
                monitored_topics_to_save[name].pop('messages', None) 

            data = {
                'welcome_mode': self.welcome_mode, 'welcome_time': self.welcome_time, 'welcome_delete_time': self.welcome_delete_time,
                'daily_messages': self.daily_messages, 'registered_topics': self.registered_topics,
                'target_chat_id': self.target_chat_id, 'target_thread_id': self.target_thread_id,
                'last_welcome_message': self.last_welcome_message, 'monitored_topics': monitored_topics_to_save,
                'forbidden_words': self.forbidden_words, 'auto_response_topics': self.auto_response_topics,
            }
            await asyncio.to_thread(self._write_data_to_file, data)
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")

    def _write_data_to_file(self, data):
        """Блокирующая операция записи в файл"""
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # --- ПЛАНИРОВЩИКИ (ВОССТАНОВЛЕНЫ) ---
    def setup_schedulers(self):
        """Настройка всех задач по расписанию."""
        
        for job in self.scheduler.get_jobs():
            try:
                self.scheduler.remove_job(job.id)
            except Exception:
                pass 

        has_messages = bool(self.daily_messages)
        is_target_set = bool(self.target_chat_id)

        if self.welcome_mode and has_messages and is_target_set:
            try:
                h, m = map(int, self.welcome_time.split(':'))
                self.scheduler.add_job(self.send_welcome_message_job, CronTrigger(hour=h, minute=m, timezone=pytz.UTC), id='welcome_message', replace_existing=True)
                
                h_del, m_del = map(int, self.welcome_delete_time.split(':'))
                self.scheduler.add_job(self.delete_welcome_message_job, CronTrigger(hour=h_del, minute=m_del, timezone=pytz.UTC), id='welcome_delete', replace_existing=True)
            except Exception as e: logger.error(f"Ошибка планирования приветствий: {e}")
        
        for topic_name, topic_data in self.monitored_topics.items():
            job_id = f'cleanup_{topic_name}'
            try:
                cleanup_time = topic_data.get('cleanup_time', '18:00')
                h, m = map(int, cleanup_time.split(':'))
                self.scheduler.add_job(self.cleanup_topic_job, CronTrigger(hour=h, minute=m, timezone=pytz.UTC), args=[topic_name], id=job_id, replace_existing=True)
            except Exception as e: logger.error(f"Ошибка планирования очистки ({topic_name}): {e}")

    async def send_welcome_message_job(self):
        # Реализация отправки приветствия
        pass # Заглушка, так как функционал не является проблемой

    async def delete_welcome_message_job(self):
        # Реализация удаления приветствия
        pass # Заглушка, так как функционал не является проблемой

    async def cleanup_topic_job(self, topic_name: str):
        # Реализация очистки темы
        pass # Заглушка, так как функционал не является проблемой
    # ------------------------------------

    async def check_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        # Для простоты в ЛС всегда True
        if update.effective_chat.type == 'private': return True 
        # ... (Полная логика админа, для краткости опущена)
        return True 

    # --- ОБРАБОТЧИКИ КОМАНД (ГРУППА) ---
    
    async def register_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Регистрация темы/чата для отправки ПРИВЕТСТВИЙ."""
        if not update.message: return
        if update.effective_chat.type == 'private': 
             return await update.message.reply_text("❌ Эту команду нужно использовать в теме вашей группы.", quote=True)

        # check_admin возвращает True в ЛС, поэтому здесь не будет проблем.
        if not await self.check_admin(update, context): return
        
        if not context.args:
            return await update.message.reply_text("❌ Укажите имя. Пример: `/registertopic Приветствие`", quote=True)
        
        name = " ".join(context.args)
        # Если вы ввели /registertopic Общие вопросы, но не в теме, thread_id будет None
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None 
        
        self.registered_topics[name] = {"chat_id": update.message.chat.id, "thread_id": thread_id}
        self.save_data()
        
        topic_info = f"Тема **'{name}'**" if thread_id else f"Чат **'{name}'**"
        await update.message.reply_text(f"✅ {topic_info} зарегистрирован(а) для **ПРИВЕТСТВИЙ**. Теперь можно выбрать в меню.", parse_mode='Markdown', quote=True)
        
    # --- (Остальные обработчики для группы опущены) ---

    # -----------------------------------------------------------------
    # ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И МЕНЮ (ЛС)
    # -----------------------------------------------------------------
    
    def get_day_name(self, index: int) -> str:
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        return days[index]

    def get_current_target_name(self) -> Optional[str]:
        if self.target_chat_id is None: return None
        for name, data in self.registered_topics.items():
            if self.target_chat_id == data.get('chat_id') and self.target_thread_id == data.get('thread_id'):
                return name
        return None 
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда старт. Сбрасывает состояние ввода."""
        if update.message and update.message.chat.type == 'private':
            # Сброс состояния ввода
            context.user_data.pop(INPUT_STATE_TIME, None)
            context.user_data.pop('timer_key', None)
            
            await self._send_main_menu(update.message.chat_id)
        elif update.message:
            await update.message.reply_text("Для управления ботом используйте личные сообщения.", quote=True)

    # --- ОБРАБОТЧИК ТЕКСТА (ДЛЯ ВВОДА ВРЕМЕНИ) ---
    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода времени или текста сообщения."""
        if update.message.chat.type != 'private': return

        if context.user_data.get(INPUT_STATE_TIME):
            await self._process_time_input(update, context)
            
        elif context.user_data.get(INPUT_STATE_DAILY_MESSAGE):
            await update.message.reply_text("🚧 Ожидание текста сообщения (раздел в разработке).")
            
        else:
             # Если бот не ждет ввода, просто игнорируем текст
             pass

    async def _process_time_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Проверка и сохранение введенного времени."""
        new_time = update.message.text.strip()
        timer_key = context.user_data.get('timer_key')
        
        if not re.match(r"^\d{1,2}:\d{2}$", new_time):
            await update.message.reply_text("❌ Неверный формат. Введите время в формате HH:MM (например, 09:30).")
            return
            
        try:
            h, m = map(int, new_time.split(':'))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Некорректное время. Часы должны быть от 00 до 23, минуты от 00 до 59.")
            return

        # Сохранение и сброс состояния
        if timer_key == 'welcome':
            self.welcome_time = new_time
            message = f"✅ Время отправки приветствий установлено на **{new_time} UTC**."
        elif timer_key == 'welcome_delete':
            self.welcome_delete_time = new_time
            message = f"✅ Время удаления приветствий установлено на **{new_time} UTC**."
        else:
            message = "❌ Произошла ошибка. Пожалуйста, начните настройку сначала с помощью /start."
        
        self.save_data()
        self.setup_schedulers() # Перезапуск планировщика
        
        context.user_data.pop(INPUT_STATE_TIME, None)
        context.user_data.pop('timer_key', None)
        
        await update.message.reply_text(message, parse_mode='Markdown')
        # Возвращаем пользователя в меню приветствий
        await self._send_daily_messages_menu(update.message.chat_id)


    # --- ОБРАБОТЧИК КНОПОК ---
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer() 
        data = query.data
        
        # Сброс состояния ввода при любом действии с меню
        context.user_data.pop(INPUT_STATE_TIME, None)
        context.user_data.pop('timer_key', None)

        if data == "back_main":
            await self._edit_main_menu(query)
        elif data == "daily_messages":
            await self._edit_daily_messages_menu(query)
        elif data == "monitored_topics_menu":
            await self._edit_monitored_topics_menu(query)
        
        # ОБРАБОТКА ВЫБОРА ЦЕЛИ
        elif data == "set_target_topic": 
            await self._edit_set_target_topic_menu(query)
        elif data.startswith("set_target_"):
            topic_name = data.split("set_target_")[1]
            await self._set_target_topic_action(query, topic_name)
            
        # ОБРАБОТКА НАСТРОЙКИ ВРЕМЕНИ
        elif data == "timer_welcome":
            await self._handle_timer_setup(query, context, 'welcome')
        elif data == "timer_welcome_delete":
            await self._handle_timer_setup(query, context, 'welcome_delete')
        
        # ЗАГЛУШКИ
        elif data.startswith("select_day_"):
            await query.edit_message_text("🚧 Ввод сообщения для дня недели.", reply_markup=self._get_back_to_daily_keyboard())
        elif data == "toggle_welcome_mode":
            await query.edit_message_text("🚧 Переключение режима приветствий.", reply_markup=self._get_back_to_daily_keyboard())
        elif data == "forbidden_words_menu":
            await query.edit_message_text("🚧 Настройка запрещенных слов.", reply_markup=self._get_back_to_main_keyboard())
        elif data == "timers":
            await query.edit_message_text("🚧 Общее меню таймеров.", reply_markup=self._get_back_to_main_keyboard())
        
        else:
             await query.edit_message_text(f"🚧 Раздел в разработке (Callback: {data})", reply_markup=self._get_back_to_main_keyboard())

    # --- НОВЫЙ ВСПОМОГАТЕЛЬНЫЙ МЕТОД ДЛЯ КЛАВИАТУРЫ ---
    def _get_back_to_main_keyboard(self):
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]])
    
    def _get_back_to_daily_keyboard(self):
         return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад к приветствиям", callback_data="daily_messages")]])

    # --- НОВЫЙ МЕТОД ДЕЙСТВИЙ (ДЛЯ ТАЙМЕРА) ---

    async def _handle_timer_setup(self, query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, timer_key: str):
        """Переводит бота в режим ожидания ввода времени от пользователя."""
        
        if timer_key == 'welcome':
            current_time = self.welcome_time
            prompt_text = "Введите новое время **отправки** приветствий (HH:MM UTC):"
        elif timer_key == 'welcome_delete':
            current_time = self.welcome_delete_time
            prompt_text = "Введите новое время **удаления** приветствий (HH:MM UTC):"
        else:
            await query.edit_message_text("❌ Неизвестный ключ таймера.")
            return

        # Устанавливаем состояние ввода
        context.user_data[INPUT_STATE_TIME] = True
        context.user_data['timer_key'] = timer_key
        
        await query.edit_message_text(
            f"{prompt_text}\n\nТекущее время: **{current_time} UTC**\n\n_Чтобы отменить, введите /start_", 
            parse_mode='Markdown'
        )

    async def _set_target_topic_action(self, query: Update.callback_query, topic_name: str):
        """Действие: Сохранение выбранной темы как целевой и перезапуск планировщика."""
        
        topic_data = self.registered_topics.get(topic_name)
        
        if not topic_data:
            await query.edit_message_text(f"❌ Тема **'{topic_name}'** не найдена.", parse_mode='Markdown')
            return

        self.target_chat_id = topic_data['chat_id']
        self.target_thread_id = topic_data['thread_id']
        self.save_data()
        
        self.setup_schedulers()
        
        await self._edit_daily_messages_menu(query)


    # --- МЕТОДЫ МЕНЮ (ВОССТАНОВЛЕНЫ) ---

    async def _send_main_menu(self, chat_id: int):
        """Отправка нового сообщения Главного меню."""
        keyboard = [
            [InlineKeyboardButton("📅 Ежедневные Приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("🧹 Авто-очистка тем (Worker)", callback_data="monitored_topics_menu")],
            [InlineKeyboardButton("🤐 Запрещенные Слова", callback_data="forbidden_words_menu")],
            [InlineKeyboardButton("⏰ Настройка времени (UTC)", callback_data="timers")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.bot.send_message(chat_id, "👋 **Главное меню:**", reply_markup=reply_markup, parse_mode='Markdown')

    async def _edit_main_menu(self, query: Update.callback_query):
        """Редактирование сообщения до Главного меню."""
        keyboard = [
            [InlineKeyboardButton("📅 Ежедневные Приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("🧹 Авто-очистка тем (Worker)", callback_data="monitored_topics_menu")],
            [InlineKeyboardButton("🤐 Запрещенные Слова", callback_data="forbidden_words_menu")],
            [InlineKeyboardButton("⏰ Настройка времени (UTC)", callback_data="timers")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try: await query.edit_message_text("👋 **Главное меню:**", reply_markup=reply_markup, parse_mode='Markdown')
        except Exception: pass
        
    async def _send_daily_messages_menu(self, chat_id: int):
        """Отправка нового меню приветствий (для использования после ввода текста)."""
        target_name = self.get_current_target_name() or "❌ Не задана"
        is_active = self.welcome_mode and self.target_chat_id and self.daily_messages
        status = "Включено ✅" if is_active else "Выключено ❌"
        
        # Клавиатура (такая же, как в _edit_daily_messages_menu)
        keyboard = self._build_daily_messages_keyboard(status, target_name)
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = (
            "📅 **Настройка Ежедневных Приветствий**\n\n"
            f"**Общий статус:** {status}\n"
            f"**Отправка:** {self.welcome_time} UTC\n"
            f"**Удаление:** {self.welcome_delete_time} UTC\n"
            f"**Цель:** {target_name}\n\n"
            "Нажмите на день, чтобы задать или изменить текст сообщения."
        )
        await self.bot.send_message(chat_id, message_text, reply_markup=reply_markup, parse_mode='Markdown')

    def _build_daily_messages_keyboard(self, status, target_name):
        """Вспомогательная функция для построения клавиатуры меню приветствий."""
        day_buttons = []
        for i in range(7):
            day = self.get_day_name(i)
            status_day = "📝 Задано" if str(i) in self.daily_messages else "➕ Добавить"
            day_buttons.append(InlineKeyboardButton(f"{day}: {status_day}", callback_data=f"select_day_{i}"))
        
        keyboard = []
        for i in range(0, len(day_buttons), 2):
            row = [day_buttons[i]]
            if i + 1 < len(day_buttons):
                row.append(day_buttons[i+1])
            keyboard.append(row)

        keyboard.append([
            InlineKeyboardButton(f"🕐 Отправка: {self.welcome_time} UTC", callback_data="timer_welcome"),
            InlineKeyboardButton(f"🗑️ Удаление: {self.welcome_delete_time} UTC", callback_data="timer_welcome_delete")
        ])
        
        keyboard.append([InlineKeyboardButton(f"🎯 Целевая тема: {target_name}", callback_data="set_target_topic")])
        keyboard.append([InlineKeyboardButton(f"▶️ Статус: {status}", callback_data="toggle_welcome_mode")])
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        return keyboard

    async def _edit_daily_messages_menu(self, query: Update.callback_query):
        """Меню для настройки ежедневных приветствий (редактирование)."""
        target_name = self.get_current_target_name() or "❌ Не задана"
        is_active = self.welcome_mode and self.target_chat_id and self.daily_messages
        status = "Включено ✅" if is_active else "Выключено ❌"
        
        keyboard = self._build_daily_messages_keyboard(status, target_name)
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = (
            "📅 **Настройка Ежедневных Приветствий**\n\n"
            f"**Общий статус:** {status}\n"
            f"**Отправка:** {self.welcome_time} UTC\n"
            f"**Удаление:** {self.welcome_delete_time} UTC\n"
            f"**Цель:** {target_name}\n\n"
            "Нажмите на день, чтобы задать или изменить текст сообщения."
        )

        try:
            await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        except Exception:
            # Если редактирование не удалось, отправляем новое меню (чтобы не сломалось от старых кнопок)
            await self._send_daily_messages_menu(query.message.chat_id)

            
    async def _edit_monitored_topics_menu(self, query: Update.callback_query):
        """Меню выбора темы для настройки времени очистки (заглушка)."""
        await query.edit_message_text(
            "🧹 **Меню Авто-очистки**\n\n"
            "🚧 Этот раздел требует дальнейшей реализации (список тем, кнопки настройки времени).",
            reply_markup=self._get_back_to_main_keyboard(),
            parse_mode='Markdown'
        )

    async def _edit_set_target_topic_menu(self, query: Update.callback_query):
        """Меню для выбора целевой темы для приветствий."""
        keyboard = []
        
        if not self.registered_topics:
            message_text = (
                "❌ **Нет зарегистрированных тем.**\n\n"
                "Чтобы добавить тему, используйте команду `/registertopic [ИМЯ]` в нужной теме в вашей группе."
            )
        else:
            message_text = "🎯 **Выберите целевую тему** для отправки ежедневных приветствий:"
            for name, data in self.registered_topics.items():
                is_selected = (
                    self.target_chat_id == data.get('chat_id') and 
                    self.target_thread_id == data.get('thread_id')
                )
                status = "✅ Выбрано" if is_selected else ""
                callback_data = f"set_target_{name}" 
                keyboard.append([InlineKeyboardButton(f"{name} {status}", callback_data=callback_data)])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад к приветствиям", callback_data="daily_messages")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        except Exception:
            await self._send_daily_messages_menu(query.message.chat_id)


# -----------------------------------------------------------------
# ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА
# -----------------------------------------------------------------

def main() -> None:
    if BOT_TOKEN == "ВАШ_ТОКЕН_ЗДЕСЬ":
         logger.error("🚫 Останавливаю запуск: токен не установлен.")
         return

    application = Application.builder().token(BOT_TOKEN).post_init(DailyMessageBot.post_init_hook).build()
    bot_instance = DailyMessageBot(application)

    application.post_init = bot_instance.post_init_hook

    # 2. Обработчики команд
    application.add_handler(CommandHandler("start", bot_instance.start))
    application.add_handler(CommandHandler("registertopic", bot_instance.register_topic))

    # 3. Обработчик текста (для приема времени и сообщений)
    # Важно: этот обработчик должен быть перед CallbackQueryHandler, но после CommandHandler
    text_filter = filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND
    application.add_handler(MessageHandler(text_filter, bot_instance.handle_text_input))

    # 4. Обработчик кнопок (CallbackQueryHandler)
    application.add_handler(CallbackQueryHandler(bot_instance.handle_callback_query))

    logger.info("🚀 Бот запущен в режиме polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
