import logging
import json
import os
from datetime import datetime, time
import asyncio
from functools import wraps

# --- ИМПОРТЫ ДЛЯ PTB v20 ---
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters,
    ContextTypes
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

# -----------------------------------------------------------------------------
# ВАЖНАЯ НАСТРОЙКА
# -----------------------------------------------------------------------------
# ⚠️ Токен считывается из переменной окружения BOT_TOKEN (рекомендуется для хостинга)
# BOT_TOKEN = "YOUR_BOT_TOKEN_HERE" 
BOT_TOKEN = os.environ.get("8525784017:AAFLa_6Guk5_w4TekVQqjSxVliOFiPk9CXA", "DEFAULT_IF_NOT_SET") 
if BOT_TOKEN == "DEFAULT_IF_NOT_SET":
    logger.error("Переменная окружения BOT_TOKEN не найдена. Убедитесь, что она установлена в настройках хостинга или прописана в коде.")
# -----------------------------------------------------------------------------

# Константы для состояний ввода
INPUT_STATE_TIME = 'TIMER_INPUT'
INPUT_STATE_DAILY_MESSAGE = 'DAILY_MESSAGE_INPUT'
INPUT_STATE_CLEANUP_TIME = 'CLEANUP_TIMER_INPUT'


class DailyMessageBot:
    def __init__(self, application: Application):
        self.application = application
        self.bot = application.bot
        self.data_file = "bot_data.json"
        
        # Настройки режимов и времени по умолчанию
        self.silent_mode = False
        self.silent_start_time = "18:30"
        self.silent_end_time = "08:00"
        self.welcome_mode = True
        self.welcome_time = "09:00"
        self.welcome_delete_time = "10:00"
        
        # Хранилища
        self.daily_messages = {} # {день_недели(0-6): "текст сообщения"}
        self.registered_topics = {} 
        self.target_chat_id = None  
        self.target_thread_id = None 
        self.last_welcome_message = {} 
        self.monitored_topics = {} # {имя: {chat_id: int, thread_id: int/None, cleanup_time: str, messages: [list]}}
        self.forbidden_words = []
        
        self.admin_cache = {}
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)
        self.load_data()
        
    async def post_init_hook(self, application: Application):
        """Хук для запуска планировщика, когда цикл событий готов."""
        self.setup_schedulers()
        if not self.scheduler.running:
            try:
                self.scheduler.start()
                logger.info("✅ Планировщик apscheduler успешно запущен.")
            except Exception as e:
                logger.error(f"Ошибка запуска планировщика: {e}")
        
    def load_data(self):
        """Загрузка данных из файла"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, default in [
                        ('silent_mode', False), ('silent_start_time', "18:30"), ('silent_end_time', "08:00"),
                        ('welcome_mode', True), ('welcome_time', "09:00"), ('welcome_delete_time', "10:00"),
                        ('daily_messages', {}), ('registered_topics', {}), ('target_chat_id', None),
                        ('target_thread_id', None), ('last_welcome_message', {}), ('monitored_topics', {}),
                        ('forbidden_words', [])
                    ]:
                        setattr(self, key, data.get(key, default))
                    
                    # Инициализация списка сообщений, если его нет (для работы)
                    for name in self.monitored_topics:
                        if 'messages' not in self.monitored_topics[name]:
                            self.monitored_topics[name]['messages'] = []
                        if 'cleanup_time' not in self.monitored_topics[name]:
                            self.monitored_topics[name]['cleanup_time'] = "18:00"


        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
    
    def save_data(self):
        """Сохранение данных в файл (асинхронно)."""
        try:
            asyncio.run_coroutine_threadsafe(self._save_data_async(), self.application.loop)
        except Exception as e:
            logger.error(f"Ошибка инициирования сохранения данных: {e}")

    async def _save_data_async(self):
        """Асинхронное сохранение данных"""
        try:
            monitored_topics_to_save = {}
            # Удаляем 'messages' перед сохранением, так как это временные данные
            for name, data in self.monitored_topics.items():
                monitored_topics_to_save[name] = data.copy()
                monitored_topics_to_save[name].pop('messages', None) 

            data = {
                'silent_mode': self.silent_mode, 'silent_start_time': self.silent_start_time, 'silent_end_time': self.silent_end_time,
                'welcome_mode': self.welcome_mode, 'welcome_time': self.welcome_time, 'welcome_delete_time': self.welcome_delete_time,
                'daily_messages': self.daily_messages, 'registered_topics': self.registered_topics,
                'target_chat_id': self.target_chat_id, 'target_thread_id': self.target_thread_id,
                'last_welcome_message': self.last_welcome_message, 'monitored_topics': monitored_topics_to_save,
                'forbidden_words': self.forbidden_words,
            }
            await asyncio.to_thread(self._write_data_to_file, data)
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")

    def _write_data_to_file(self, data):
        """Блокирующая операция записи в файл"""
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # -----------------------------------------------------------------
    # ПЛАНИРОВЩИКИ (Async)
    # -----------------------------------------------------------------
    def setup_schedulers(self):
        """Настройка всех задач по расписанию."""
        self.schedule_welcome_message()
        self.schedule_welcome_delete()
        self.schedule_monitored_cleanup()

    def schedule_welcome_message(self):
        """Планирование ежедневного приветствия."""
        try: self.scheduler.remove_job('welcome_message')
        except: pass
        try:
            hour, minute = map(int, self.welcome_time.split(':'))
            self.scheduler.add_job(self.send_welcome_message_job, CronTrigger(hour=hour, minute=minute, timezone=pytz.UTC), id='welcome_message')
            logger.info(f"✅ Приветствие запланировано на: {self.welcome_time} UTC")
        except Exception as e: logger.error(f"Ошибка schedule_welcome_message: {e}")

    def schedule_welcome_delete(self):
        """Планирование удаления приветствия."""
        try: self.scheduler.remove_job('welcome_delete')
        except: pass
        try:
            hour, minute = map(int, self.welcome_delete_time.split(':'))
            self.scheduler.add_job(self.delete_welcome_message_job, CronTrigger(hour=hour, minute=minute, timezone=pytz.UTC), id='welcome_delete')
            logger.info(f"✅ Удаление приветствия запланировано на: {self.welcome_delete_time} UTC")
        except Exception as e: logger.error(f"Ошибка schedule_welcome_delete: {e}")

    def schedule_monitored_cleanup(self):
        """Планирование очистки для всех отслеживаемых тем."""
        # Удаляем все старые задания очистки
        for job in self.scheduler.get_jobs():
            if job.id.startswith('cleanup_'):
                self.scheduler.remove_job(job.id)
                
        # Создаем новые задания
        for topic_name, topic_data in self.monitored_topics.items():
            job_id = f'cleanup_{topic_name}'
            try:
                cleanup_time = topic_data.get('cleanup_time', '18:00')
                hour, minute = map(int, cleanup_time.split(':'))
                self.scheduler.add_job(self.cleanup_topic_job, CronTrigger(hour=hour, minute=minute, timezone=pytz.UTC), args=[topic_name], id=job_id)
                logger.info(f"✅ Очистка '{topic_name}' запланирована на: {cleanup_time} UTC")
            except Exception as e: logger.error(f"Ошибка schedule_monitored_cleanup ({topic_name}): {e}")

    async def send_welcome_message_job(self):
        """Отправка ежедневного приветствия."""
        try:
            today = datetime.now(pytz.UTC).weekday()
            message = self.daily_messages.get(str(today))
            
            if not self.welcome_mode or not message or not self.target_chat_id: 
                return
            
            sent_message = await self.bot.send_message(
                chat_id=self.target_chat_id, 
                text=message, 
                message_thread_id=self.target_thread_id
            )
            self.last_welcome_message = {"chat_id": sent_message.chat_id, "message_id": sent_message.message_id}
            await self._save_data_async()
        except Exception as e: 
            logger.error(f"Ошибка send_welcome_message_job: {e}")

    async def delete_welcome_message_job(self):
        """Удаление последнего приветственного сообщения."""
        if not self.last_welcome_message: return
        try:
            await self.bot.delete_message(chat_id=self.last_welcome_message['chat_id'], message_id=self.last_welcome_message['message_id'])
        except Exception as e: 
            logger.warning(f"Не удалось удалить приветствие: {e}")
        finally:
            self.last_welcome_message = {}
            await self._save_data_async()
    
    async def get_admin_ids(self, chat_id):
        """Кэширование и получение ID администраторов."""
        now = datetime.now()
        if chat_id in self.admin_cache and (now - self.admin_cache[chat_id]['timestamp']).total_seconds() < 600:
            return self.admin_cache[chat_id]['ids']
        try:
            admins = await self.bot.get_chat_administrators(chat_id)
            admin_ids = [admin.user.id for admin in admins]
            self.admin_cache[chat_id] = {'ids': admin_ids, 'timestamp': now}
            return admin_ids
        except Exception as e:
            logger.error(f"Не удалось получить список админов: {e}")
            return []

    async def cleanup_topic_job(self, topic_name):
        """Очистка сообщений не-админов в отслеживаемой теме."""
        logger.info(f"🧹 Запуск очистки для темы: {topic_name}")
        if topic_name not in self.monitored_topics: return
            
        topic_data = self.monitored_topics[topic_name]
        chat_id = topic_data['chat_id']
        messages_to_delete = topic_data.get('messages', [])
        if not messages_to_delete: return

        admin_ids = await self.get_admin_ids(chat_id)
        if not admin_ids: 
            logger.warning(f"Не удалось получить список админов для {topic_name}. Очистка отменена.")
            return

        deleted_count = 0
        
        # NOTE: Создаем новый список, чтобы избежать проблем с параллельным доступом
        messages_to_process = list(messages_to_delete)
        
        for msg in messages_to_process:
            if msg['user_id'] not in admin_ids:
                try:
                    await self.bot.delete_message(chat_id=chat_id, message_id=msg['message_id'])
                    deleted_count += 1
                except Exception: pass
        
        logger.info(f"✅ Очистка {topic_name} завершена. Удалено {deleted_count} сообщений.")
        
        # Очищаем список после завершения работы
        self.monitored_topics[topic_name]['messages'] = []
        await self._save_data_async()

    # -----------------------------------------------------------------
    # ОБРАБОТЧИКИ ГРУППЫ
    # -----------------------------------------------------------------
    
    async def check_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        if update.effective_chat.type == 'private': return True 
        try:
            member = await context.bot.get_chat_member(chat_id=update.effective_chat.id, user_id=update.effective_user.id)
            is_admin = member.status in [ChatMember.ADMINISTRATOR, ChatMember.CREATOR]
            if not is_admin:
                await update.message.reply_text("❌ Только администраторы.")
            return is_admin
        except Exception as e:
            logger.error(f"Ошибка проверки админа: {e}")
            return False

    async def register_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message: return
        if not await self.check_admin(update, context): return
        if not context.args:
            await update.message.reply_text("❌ Укажите имя.\nПример: `/registertopic Новости`")
            return
        
        name = " ".join(context.args)
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        
        self.registered_topics[name] = {"chat_id": update.message.chat.id, "thread_id": thread_id}
        self.save_data()
        
        topic_info = f"Тема '{name}'" if thread_id else f"Чат '{name}'"
        await update.message.reply_text(f"✅ {topic_info} зарегистрирован(а) для **ПРИВЕТСТВИЙ**.", parse_mode='Markdown')

    async def register_monitor_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message: return
        if not await self.check_admin(update, context): return
        if not context.args:
            await update.message.reply_text("❌ Укажите имя.\nПример: `/monitorcleanup Флудилка`")
            return
        
        name = " ".join(context.args)
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        
        # Инициализируем данные для темы
        self.monitored_topics[name] = {
            "chat_id": update.message.chat.id, 
            "thread_id": thread_id,
            "cleanup_time": self.monitored_topics.get(name, {}).get('cleanup_time', "18:00"), # Сохраняем, если уже было, иначе дефолт
            "messages": []
        }
        self.save_data()
        self.schedule_monitored_cleanup() # Перепланируем задачи
        
        topic_info = f"Тема '{name}'" if thread_id else f"Чат '{name}'"
        await update.message.reply_text(f"✅ {topic_info} зарегистрирован(а) для **АВТО-ОЧИСТКИ** (не-админские сообщения будут удаляться в {self.monitored_topics[name]['cleanup_time']} UTC).", parse_mode='Markdown')

    def get_monitored_topic_name(self, chat_id, thread_id):
        for name, data in self.monitored_topics.items():
            if data['chat_id'] == chat_id and data['thread_id'] == thread_id:
                return name
        return None
    
    async def handle_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message: return
        
        # 1. Стоп-слова и Режим тишины (опущено для фокуса, но должно быть здесь)
        
        # 2. Сбор сообщений для авто-очистки
        topic_name = self.get_monitored_topic_name(update.message.chat_id, update.message.message_thread_id)
        if topic_name and update.message.message_id and update.message.from_user:
            # Сохраняем ID сообщения и ID пользователя для последующей очистки
            self.monitored_topics[topic_name]['messages'].append({
                "message_id": update.message.message_id, 
                "user_id": update.message.from_user.id
            })

    # -----------------------------------------------------------------
    # УТИЛИТЫ ДЛЯ МЕНЮ
    # -----------------------------------------------------------------
    
    def get_day_name(self, index):
        """Возвращает название дня недели по индексу (0=Пн, 6=Вс)."""
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        return days[index]

    def get_current_target_name(self):
        """Получение имени целевой темы для приветствий."""
        if not self.target_chat_id: return None
        for name, data in self.registered_topics.items():
            if self.target_chat_id == data['chat_id'] and self.target_thread_id == data['thread_id']:
                return name
        return None 
    
    def get_monitored_topic_name_by_ids(self, chat_id, thread_id):
        """Получение имени мониторируемой темы по chat_id и thread_id."""
        for name, data in self.monitored_topics.items():
            if data['chat_id'] == chat_id and data['thread_id'] == thread_id:
                return name
        return None

    # -----------------------------------------------------------------
    # ФУНКЦИИ ОТОБРАЖЕНИЯ МЕНЮ (ЛС)
    # -----------------------------------------------------------------

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда старт."""
        if update.message and update.message.chat.type == 'private':
            await self._send_main_menu(update.message.chat_id)
        elif update.message:
             await update.message.reply_text("Для управления ботом используйте личные сообщения.", quote=True)

    async def _send_main_menu(self, chat_id):
        """Отправка нового сообщения Главного меню (команда /start)."""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени (UTC)", callback_data="timers")],
            [InlineKeyboardButton("📅 Ежедневные приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("🧹 Темы с авто-очисткой", callback_data="monitored_topics_menu")],
            [InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.bot.send_message(chat_id, "👋 Главное меню:", reply_markup=reply_markup)

    async def _edit_main_menu(self, query):
        """Редактирование сообщения до Главного меню (кнопка 'Назад')."""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени (UTC)", callback_data="timers")],
            [InlineKeyboardButton("📅 Ежедневные приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("🧹 Темы с авто-очисткой", callback_data="monitored_topics_menu")],
            [InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try: await query.edit_message_text("👋 Главное меню:", reply_markup=reply_markup)
        except Exception: pass
        
    # --- МЕНЮ АВТО-ОЧИСТКИ (НОВОЕ) ---
    
    async def _edit_monitored_topics_menu(self, query):
        """Меню выбора темы для настройки времени очистки."""
        if not self.monitored_topics:
            keyboard = [[InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]]
            return await query.edit_message_text(
                "❌ **Нет зарегистрированных тем для авто-очистки.**\n\n"
                "Чтобы добавить тему, используйте команду `/monitorcleanup [ИМЯ ТЕМЫ]` в нужной теме в вашей группе.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        keyboard = []
        for name, data in self.monitored_topics.items():
            cleanup_time = data.get('cleanup_time', '18:00')
            keyboard.append([InlineKeyboardButton(f"🧹 {name} ({cleanup_time} UTC)", callback_data=f"select_monitor_{name}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🧹 **Настройка авто-очистки**\n\n"
            "Выберите тему, чтобы изменить время ежедневной очистки.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def _edit_cleanup_time_menu(self, query, topic_name):
        """Меню настройки времени очистки для конкретной темы."""
        data = self.monitored_topics.get(topic_name)
        if not data:
            await query.answer("❌ Тема не найдена.", show_alert=True)
            return await self._edit_monitored_topics_menu(query)
            
        current_time = data.get('cleanup_time', '18:00')
        
        keyboard = [
            [InlineKeyboardButton(f"⏰ Изменить время: {current_time} UTC", callback_data=f"set_cleanup_time_{topic_name}")],
            [InlineKeyboardButton(f"🗑️ Удалить '{topic_name}' из мониторинга", callback_data=f"delete_monitor_{topic_name}")],
            [InlineKeyboardButton("🔙 Назад к списку тем", callback_data="back_monitor")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🛠️ **Настройка очистки для темы '{topic_name}'**\n\n"
            f"Текущее время ежедневной очистки (удаление не-админских сообщений) установлено на **{current_time} UTC**.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    # --- ЕЖЕДНЕВНЫЕ ПРИВЕТСТВИЯ (Остаются без изменений) ---
    
    async def _send_daily_messages_menu(self, chat_id):
        """Отправка НОВОГО сообщения меню ежедневных приветствий (после ввода текста)."""
        target_name = self.get_current_target_name() or "❌ Не задана"
        
        day_buttons = []
        for i in range(7):
            day = self.get_day_name(i)
            status = "📝 Задано" if str(i) in self.daily_messages else "➕ Добавить"
            day_buttons.append(InlineKeyboardButton(f"{day}: {status}", callback_data=f"select_day_{i}"))
        
        keyboard = []
        for i in range(0, len(day_buttons), 2):
            row = [day_buttons[i]]
            if i + 1 < len(day_buttons):
                row.append(day_buttons[i+1])
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton(f"🎯 Целевая тема: {target_name}", callback_data="set_target_topic")])
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.bot.send_message(
            chat_id,
            "📅 **Ежедневные приветствия**\n\n"
            "Установите текст, который будет отправляться каждый день по расписанию (0=Пн, 6=Вс).",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    async def _edit_daily_messages_menu(self, query):
        # ... (Код аналогичен _send_daily_messages_menu, но использует query.edit_message_text)
        target_name = self.get_current_target_name() or "❌ Не задана"
        
        day_buttons = []
        for i in range(7):
            day = self.get_day_name(i)
            status = "📝 Задано" if str(i) in self.daily_messages else "➕ Добавить"
            day_buttons.append(InlineKeyboardButton(f"{day}: {status}", callback_data=f"select_day_{i}"))
        
        keyboard = []
        for i in range(0, len(day_buttons), 2):
            row = [day_buttons[i]]
            if i + 1 < len(day_buttons):
                row.append(day_buttons[i+1])
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton(f"🎯 Целевая тема: {target_name}", callback_data="set_target_topic")])
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "📅 **Ежедневные приветствия**\n\n"
            "Установите текст, который будет отправляться каждый день по расписанию (0=Пн, 6=Вс).",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def _edit_daily_message_day_menu(self, query, day_index):
        # ... (Код из предыдущей версии)
        day_name = self.get_day_name(day_index)
        current_message = self.daily_messages.get(str(day_index), "*Сообщение не задано.*")
        
        keyboard = [
            [InlineKeyboardButton("📝 Задать/Изменить текст", callback_data=f"set_message_{day_index}")],
        ]
        if str(day_index) in self.daily_messages:
             keyboard.append([InlineKeyboardButton("🗑️ Удалить сообщение", callback_data=f"delete_message_{day_index}")])

        keyboard.append([InlineKeyboardButton("🔙 Назад к дням", callback_data="back_daily")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Обрезаем сообщение для отображения в меню
        display_message = current_message
        if len(current_message) > 200:
            display_message = current_message[:200] + "..."

        await query.edit_message_text(
            f"📅 **Сообщение для {day_name}**\n\n"
            f"**Текущий текст:**\n"
            f"```\n{display_message}\n```",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    async def _edit_target_topic_menu(self, query):
        # ... (Код из предыдущей версии)
        if not self.registered_topics:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_daily")]]
            return await query.edit_message_text(
                "❌ **Нет зарегистрированных тем.**\n\n"
                "Чтобы задать целевую тему, сначала используйте команду `/registertopic [ИМЯ ТЕМЫ/ЧАТА]` в нужной теме (или чате) в вашей группе.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        keyboard = []
        for name, data in self.registered_topics.items():
            is_current = (self.target_chat_id == data['chat_id'] and self.target_thread_id == data['thread_id'])
            status = "✅ Выбрана" if is_current else "➡️ Выбрать"
            keyboard.append([InlineKeyboardButton(f"{name} ({status})", callback_data=f"select_topic:{name}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_daily")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "🎯 **Выберите целевую тему**\n\n"
            "Сюда будут отправляться ежедневные приветственные сообщения.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )


    # -----------------------------------------------------------------
    # ГЕНЕРАЛЬНЫЙ CALLBACK-ОБРАБОТЧИК
    # -----------------------------------------------------------------

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        
        # Очистка состояния ввода при нажатии навигационных кнопок
        if data in ["back_main", "daily_messages", "back_monitor"]:
            context.user_data.pop('next_action', None)
            context.user_data.pop('day_index', None)
            context.user_data.pop('monitor_topic_name', None)


        # --- НАВИГАЦИЯ ---
        if data == "back_main":
            return await self._edit_main_menu(query)
        elif data == "daily_messages" or data == "back_daily":
            return await self._edit_daily_messages_menu(query)
        elif data == "monitored_topics_menu" or data == "back_monitor":
            return await self._edit_monitored_topics_menu(query)
        
        # --- МЕНЮ АВТО-ОЧИСТКИ ---
        elif data.startswith("select_monitor_"):
            topic_name = data.split("_")[-1]
            return await self._edit_cleanup_time_menu(query, topic_name)
        
        elif data.startswith("set_cleanup_time_"):
            topic_name = data.split("_")[-1]
            
            # Установка состояния ожидания ввода
            context.user_data['next_action'] = INPUT_STATE_CLEANUP_TIME
            context.user_data['monitor_topic_name'] = topic_name
            
            await query.edit_message_text(
                f"⏰ **Введите новое время очистки для темы '{topic_name}' в формате ЧЧ:ММ (UTC).**\n\n"
                f"Например, `19:30`. Для отмены нажмите /start.",
                parse_mode='Markdown'
            )
        
        elif data.startswith("delete_monitor_"):
            topic_name = data.split("_")[-1]
            
            if topic_name in self.monitored_topics:
                del self.monitored_topics[topic_name]
                self.save_data()
                self.schedule_monitored_cleanup() # Перепланируем задачи, чтобы удалить job
                await query.answer(f"🗑️ Тема '{topic_name}' удалена из авто-очистки.", show_alert=True)
            
            return await self._edit_monitored_topics_menu(query)

        # --- ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (Приветствия, Таймеры) ---
        
        elif data.startswith("select_day_"):
            day_index = int(data.split("_")[-1])
            context.user_data['day_index'] = day_index
            return await self._edit_daily_message_day_menu(query, day_index)
        
        elif data.startswith("set_message_"):
            day_index = int(data.split("_")[-1])
            day_name = self.get_day_name(day_index)
            context.user_data['next_action'] = INPUT_STATE_DAILY_MESSAGE
            context.user_data['day_index'] = day_index
            await query.edit_message_text(
                f"📝 **Введите текст приветствия для {day_name}**.\n\n"
                f"Отправьте мне сообщение, и оно будет сохранено. Для отмены нажмите /start.",
                parse_mode='Markdown'
            )
        
        elif data.startswith("delete_message_"):
            day_index = str(data.split("_")[-1])
            day_name = self.get_day_name(int(day_index))
            if day_index in self.daily_messages:
                del self.daily_messages[day_index]
                self.save_data()
                await query.answer(f"🗑️ Сообщение для {day_name} удалено!", show_alert=True)
            await self._edit_daily_message_day_menu(query, int(day_index))

        elif data == "set_target_topic":
            return await self._edit_target_topic_menu(query)

        elif data.startswith("select_topic:"):
            topic_name = data.split(":")[1]
            data = self.registered_topics.get(topic_name)
            if data:
                self.target_chat_id = data['chat_id']
                self.target_thread_id = data['thread_id']
                self.save_data()
                await query.answer(f"✅ Целевая тема '{topic_name}' установлена!", show_alert=True)
            else:
                await query.answer("❌ Ошибка: Тема не найдена.", show_alert=True)
            return await self._edit_daily_messages_menu(query)


    # -----------------------------------------------------------------
    # ОБРАБОТЧИК ТЕКСТОВОГО ВВОДА (ДЛЯ СОСТОЯНИЙ)
    # -----------------------------------------------------------------

    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or update.message.chat.type != 'private': return
        
        if 'next_action' not in context.user_data:
            return 
        
        user_input = update.message.text.strip()
        action = context.user_data.pop('next_action')
        chat_id = update.message.chat_id

        # --- Обработка ввода для ежедневного сообщения ---
        if action == INPUT_STATE_DAILY_MESSAGE:
            day_index = context.user_data.pop('day_index')
            day_name = self.get_day_name(day_index)
            
            self.daily_messages[str(day_index)] = user_input
            self.save_data()
            
            await self.bot.send_message(
                chat_id,
                f"✅ **Сообщение для {day_name} успешно сохранено!**",
                parse_mode='Markdown'
            )
            await self._send_daily_messages_menu(chat_id)

        # --- Обработка ввода для времени (ОБЩЕЕ) ---
        elif action in [INPUT_STATE_TIME, INPUT_STATE_CLEANUP_TIME]:
            
            if not len(user_input) == 5 or user_input[2] != ':' or not user_input.replace(':', '').isdigit():
                await update.message.reply_text("❌ Неверный формат. Используйте **ЧЧ:ММ** (например, `09:30`).")
                context.user_data['next_action'] = action # Возвращаем состояние
                return

            try:
                time.fromisoformat(user_input)
                
                # Обновление времени приветствия/удаления
                if action == INPUT_STATE_TIME:
                    timer_key = context.user_data.pop('timer_key')
                    setattr(self, timer_key, user_input)
                    self.save_data()
                    self.setup_schedulers()
                    await self.bot.send_message(chat_id, f"✅ **Время для `{timer_key}` обновлено на {user_input} UTC.**", parse_mode='Markdown')
                    # await self._send_timers_menu(chat_id) # Заменить на реальный вызов
                
                # Обновление времени авто-очистки
                elif action == INPUT_STATE_CLEANUP_TIME:
                    topic_name = context.user_data.pop('monitor_topic_name')
                    if topic_name in self.monitored_topics:
                        self.monitored_topics[topic_name]['cleanup_time'] = user_input
                        self.save_data()
                        self.schedule_monitored_cleanup()
                        await self.bot.send_message(chat_id, f"✅ **Время очистки для '{topic_name}' обновлено на {user_input} UTC.**", parse_mode='Markdown')
                        await self._edit_monitored_topics_menu_after_input(chat_id)
                    else:
                        await update.message.reply_text("❌ Ошибка: Тема мониторинга не найдена.")

            except ValueError:
                await update.message.reply_text("❌ Некорректное время. Проверьте ЧЧ (00-23) и ММ (00-59).")
                context.user_data['next_action'] = action # Возвращаем состояние
            
    async def _edit_monitored_topics_menu_after_input(self, chat_id):
        """Отправка нового меню мониторинга после текстового ввода."""
        
        if not self.monitored_topics:
            keyboard = [[InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]]
            return await self.bot.send_message(chat_id, "❌ **Нет зарегистрированных тем для авто-очистки.**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

        keyboard = []
        for name, data in self.monitored_topics.items():
            cleanup_time = data.get('cleanup_time', '18:00')
            keyboard.append([InlineKeyboardButton(f"🧹 {name} ({cleanup_time} UTC)", callback_data=f"select_monitor_{name}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.bot.send_message(
            chat_id,
            "🧹 **Настройка авто-очистки**\n\n"
            "Выберите тему, чтобы изменить время ежедневной очистки.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    # -----------------------------------------------------------------
    # ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА
    # -----------------------------------------------------------------
    def run(self):
        """Запуск бота."""
        application = Application.builder().token(BOT_TOKEN).post_init(self.post_init_hook).build()

        # 1. Команды
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("registertopic", self.register_topic))
        application.add_handler(CommandHandler("monitorcleanup", self.register_monitor_topic))

        # 2. Обработчик нажатий кнопок
        application.add_handler(CallbackQueryHandler(self.handle_callback_query))

        # 3. Обработчик всех текстовых сообщений (для ввода данных в меню)
        application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, self.handle_text_input))
        
        # 4. Обработчик сообщений в группе (для сбора сообщений для очистки)
        application.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, self.handle_group_message))


        logger.info("🤖 Бот запущен...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    # Эта часть запускается, если токен не подтянут из окружения (для локального теста)
    if BOT_TOKEN == "DEFAULT_IF_NOT_SET":
        logger.error("Запуск остановлен. Пожалуйста, установите BOT_TOKEN.")
    else:
        bot_instance = DailyMessageBot(Application.builder().token(BOT_TOKEN).build())
        bot_instance.run()
