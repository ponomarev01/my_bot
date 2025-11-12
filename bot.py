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
        
        self.welcome_mode = True
        self.welcome_time = "09:00" # Время отправки (UTC)
        self.welcome_delete_time = "10:00" # Время удаления (UTC)
        self.daily_messages: Dict[str, str] = {} 
        self.registered_topics: Dict[str, Dict[str, Any]] = {} 
        self.target_chat_id: Optional[int] = None 
        self.target_thread_id: Optional[int] = None 
        self.last_welcome_message: Dict[str, int] = {} 
        
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
        
    # --- СОХРАНЕНИЕ / ЗАГРУЗКА ДАННЫХ --- (Оставлены как заглушки для краткости)
    def load_data(self):
        # ... (логика загрузки)
        pass
    
    def save_data(self):
        # ... (логика сохранения)
        pass

    async def _save_data_async(self):
        # ... (логика асинхронного сохранения)
        pass

    def _write_data_to_file(self, data):
        # ... (логика записи в файл)
        pass
    # ------------------------------------
    
    # --- ПЛАНИРОВЩИКИ (ЗАГЛУШКИ) ---
    def setup_schedulers(self):
        # ... (логика настройки планировщика)
        pass

    async def send_welcome_message_job(self):
        # ... (логика отправки приветствия)
        pass

    async def delete_welcome_message_job(self):
        # ... (логика удаления приветствия)
        pass

    async def cleanup_topic_job(self, topic_name: str):
        # ... (логика очистки темы)
        pass
    # ------------------------------------

    async def get_admin_ids(self, chat_id: int) -> List[int]:
        # ... (логика получения админов)
        return []

    async def check_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        # ... (логика проверки админа)
        return True # В ЛС всегда True

    # --- ОБРАБОТЧИКИ КОМАНД (ГРУППА) ---
    
    async def register_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message: return
        if update.effective_chat.type == 'private': 
             return await update.message.reply_text("❌ Эту команду нужно использовать в теме вашей группы.", quote=True)

        if not await self.check_admin(update, context): return
        
        if not context.args:
            return await update.message.reply_text("❌ Укажите имя. Пример: `/registertopic Приветствие`", quote=True)
        
        name = " ".join(context.args)
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
            context.user_data.pop(INPUT_STATE_TIME, None)
            context.user_data.pop('timer_key', None)
            
            await self._send_main_menu(update.message.chat_id)
        elif update.message:
            await update.message.reply_text("Для управления ботом используйте личные сообщения.", quote=True)

    # --- ОБРАБОТЧИК ТЕКСТА (НОВЫЙ) ---
    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода времени или текста сообщения."""
        if update.message.chat.type != 'private': return

        if context.user_data.get(INPUT_STATE_TIME):
            await self._process_time_input(update, context)
            
        elif context.user_data.get(INPUT_STATE_DAILY_MESSAGE):
            # Тут будет обработка текста для ежедневного сообщения
            await update.message.reply_text("🚧 Ожидание текста сообщения (раздел в разработке).")
            
        else:
             # Если бот не ждет ввода, просто игнорируем текст
             pass

    async def _process_time_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Проверка и сохранение введенного времени."""
        new_time = update.message.text.strip()
        timer_key = context.user_data.get('timer_key')
        
        # Проверка формата HH:MM
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
        await self._send_daily_messages_menu(update.message.chat_id)


    # --- ОБРАБОТЧИК КНОПОК ---
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        # query.answer() нужно вызывать до edit_message_text, чтобы избежать ошибок
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
            
        # ОБРАБОТКА НАСТРОЙКИ ВРЕМЕНИ (НОВОЕ)
        elif data == "timer_welcome":
            await self._handle_timer_setup(query, context, 'welcome')
        elif data == "timer_welcome_delete":
            await self._handle_timer_setup(query, context, 'welcome_delete')
        
        # ЗАГЛУШКИ
        elif data.startswith("select_day_"):
            await query.edit_message_text("🚧 Ввод сообщения для дня недели.")
        elif data == "toggle_welcome_mode":
            await query.edit_message_text("🚧 Переключение режима приветствий.")
        elif data == "forbidden_words_menu":
            await query.edit_message_text("🚧 Настройка запрещенных слов.")
        elif data == "timers":
            await query.edit_message_text("🚧 Общее меню таймеров.")
        
        else:
             await query.edit_message_text(f"🚧 Раздел в разработке (Callback: {data})") 
             
    # --- НОВЫЕ МЕТОДЫ ДЕЙСТВИЙ (ДЛЯ ТАЙМЕРА) ---

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
        
        # Отправляем новое сообщение, ожидая ответ
        await query.edit_message_text(
            f"{prompt_text}\n\nТекущее время: **{current_time} UTC**", 
            parse_mode='Markdown'
        )

    async def _set_target_topic_action(self, query: Update.callback_query, topic_name: str):
        """Действие: Сохранение выбранной темы как целевой и перезапуск планировщика."""
        # ... (логика выбора цели)
        pass # Заглушка, чтобы не усложнять код


    # --- МЕТОДЫ МЕНЮ --- (Используется _send_ для создания нового, _edit_ для редактирования)

    async def _send_main_menu(self, chat_id: int):
        # ... (логика отправки главного меню)
        pass

    async def _edit_main_menu(self, query: Update.callback_query):
        # ... (логика редактирования главного меню)
        pass
        
    async def _send_daily_messages_menu(self, chat_id: int):
        """Отправка нового сообщения с меню приветствий (для возврата после ввода)."""
        # Логика меню такая же, как в _edit_daily_messages_menu, но вызывается send_message
        await self._send_main_menu(chat_id) # Упрощено для примера

    async def _edit_daily_messages_menu(self, query: Update.callback_query):
        """Меню для настройки ежедневных приветствий (редактирование)."""
        target_name = self.get_current_target_name() or "❌ Не задана"
        is_active = self.welcome_mode and self.target_chat_id and self.daily_messages
        status = "Включено ✅" if is_active else "Выключено ❌"
        
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
            # Здесь происходит редактирование сообщения!
            await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        except Exception as e:
            logger.warning(f"Ошибка редактирования меню: {e}. Возможно, сообщение уже было изменено или слишком старое.")
            # Если редактирование не удалось (т.к. нажата старая кнопка), отправляем новое меню.
            await self._send_main_menu(query.message.chat_id)


    async def _edit_monitored_topics_menu(self, query: Update.callback_query):
        # ... (логика меню мониторинга)
        pass 

    async def _edit_set_target_topic_menu(self, query: Update.callback_query):
        # ... (логика меню выбора цели)
        pass


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
    # и должен фильтровать только текстовые сообщения в ЛС
    text_filter = filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND
    application.add_handler(MessageHandler(text_filter, bot_instance.handle_text_input))

    # 4. Обработчик кнопок (CallbackQueryHandler)
    application.add_handler(CallbackQueryHandler(bot_instance.handle_callback_query))

    logger.info("🚀 Бот запущен в режиме polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
