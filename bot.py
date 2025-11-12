import logging
import json
import os
import sys
import asyncio
import re 
from datetime import datetime, time
from typing import Dict, Any

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
# ВАЖНАЯ НАСТРОЙКА ТОКЕНА
# -----------------------------------------------------------------------------
# Токен считывается из переменной окружения BOT_TOKEN
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
if not BOT_TOKEN:
    logger.error("Переменная окружения BOT_TOKEN не найдена. Убедитесь, что она установлена в настройках хостинга или прописана в коде.")
    sys.exit(1)
# -----------------------------------------------------------------------------

# Константы для состояний ввода
INPUT_STATE_TIME = 'TIMER_INPUT'
INPUT_STATE_DAILY_MESSAGE = 'DAILY_MESSAGE_INPUT'
INPUT_STATE_CLEANUP_TIME = 'CLEANUP_TIMER_INPUT'
INPUT_STATE_FORBIDDEN_WORDS = 'FORBIDDEN_WORDS_INPUT'


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
        self.registered_topics: Dict[str, Dict[str, Any]] = {} # {имя: {chat_id, thread_id}} - для выбора цели приветствия
        self.target_chat_id = None  
        self.target_thread_id = None 
        self.last_welcome_message: Dict[str, int] = {} # Для хранения ID последнего сообщения
        
        # Настройки авто-очистки
        self.monitored_topics: Dict[str, Dict[str, Any]] = {} # {имя: {chat_id, thread_id, cleanup_time, messages: []}}
        
        # Настройки запрещенных слов
        self.forbidden_words: list = [] # Список слов для немедленного удаления
        
        # Настройки авто-ответа "ОК"
        self.auto_response_topics: Dict[str, str] = {} # {chat_id_thread_id: "Текст ответа"}
        
        self.admin_cache: Dict[int, Dict[str, Any]] = {} # Кэш администраторов
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

                    # Загрузка и инициализация мониторинга
                    loaded_monitored = data.get('monitored_topics', {})
                    for name in loaded_monitored:
                        # Инициализация 'messages' при загрузке, так как они не сохраняются
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
            # Удаляем 'messages' перед сохранением
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
            # Используем asyncio.to_thread для блокирующей операции
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
        
        # Удаление старых задач
        for job in self.scheduler.get_jobs():
            self.scheduler.remove_job(job.id)

        # 1. Приветствие и удаление
        has_messages = bool(self.daily_messages)
        is_target_set = bool(self.target_chat_id)

        if self.welcome_mode and has_messages and is_target_set:
            try:
                # Отправка
                h, m = map(int, self.welcome_time.split(':'))
                self.scheduler.add_job(self.send_welcome_message_job, CronTrigger(hour=h, minute=m, timezone=pytz.UTC), id='welcome_message')
                logger.info(f"✅ Приветствие запланировано на: {self.welcome_time} UTC")
                
                # Удаление
                h_del, m_del = map(int, self.welcome_delete_time.split(':'))
                self.scheduler.add_job(self.delete_welcome_message_job, CronTrigger(hour=h_del, minute=m_del, timezone=pytz.UTC), id='welcome_delete')
                logger.info(f"✅ Удаление приветствия запланировано на: {self.welcome_delete_time} UTC")
            except Exception as e: logger.error(f"Ошибка планирования приветствий: {e}")
        
        # 2. Очистка мониторируемых тем
        for topic_name, topic_data in self.monitored_topics.items():
            job_id = f'cleanup_{topic_name}'
            try:
                cleanup_time = topic_data.get('cleanup_time', '18:00')
                h, m = map(int, cleanup_time.split(':'))
                self.scheduler.add_job(self.cleanup_topic_job, CronTrigger(hour=h, minute=m, timezone=pytz.UTC), args=[topic_name], id=job_id)
                logger.info(f"✅ Очистка '{topic_name}' запланирована на: {cleanup_time} UTC")
            except Exception as e: logger.error(f"Ошибка планирования очистки ({topic_name}): {e}")


    async def send_welcome_message_job(self):
        """Отправка ежедневного приветствия."""
        try:
            today = datetime.now(pytz.UTC).weekday() # Понедельник = 0, Воскресенье = 6
            message = self.daily_messages.get(str(today))
            
            if not message or not self.target_chat_id: 
                return
            
            sent_message = await self.bot.send_message(
                chat_id=self.target_chat_id, 
                text=message, 
                message_thread_id=self.target_thread_id,
                parse_mode='Markdown'
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
        # Кэшируем на 10 минут
        cache_data = self.admin_cache.get(chat_id)
        
        if cache_data and (now - cache_data.get('timestamp', now)).total_seconds() < 600:
            return cache_data['ids']
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
        
        if not messages_to_delete: 
            logger.info(f"Очистка: Нет сообщений для удаления в {topic_name}.")
            return

        # Получаем админов один раз
        admin_ids = await self.get_admin_ids(chat_id)
        if not admin_ids: 
            logger.warning(f"Не удалось получить список админов для {topic_name}. Очистка отложена.")
            return

        deleted_count = 0
        
        # Удаление в цикле
        for msg in messages_to_delete:
            # Удаляем, если пользователь не администратор
            if msg['user_id'] not in admin_ids:
                try:
                    await self.bot.delete_message(chat_id=chat_id, message_id=msg['message_id'], message_thread_id=topic_data['thread_id'])
                    deleted_count += 1
                except Exception as e: 
                    logger.debug(f"Не удалось удалить сообщение {msg['message_id']}: {e}") 
        
        logger.info(f"✅ Очистка {topic_name} завершена. Удалено {deleted_count} сообщений.")
        
        # Очищаем список после завершения работы
        self.monitored_topics[topic_name]['messages'] = []
        await self._save_data_async()

    # -----------------------------------------------------------------
    # ОБРАБОТЧИКИ КОМАНД И СООБЩЕНИЙ (ГРУППА)
    # -----------------------------------------------------------------
    
    async def check_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Проверка прав администратора."""
        if not update.effective_user: return False
        if update.effective_chat.type == 'private': return True 

        try:
            member = await context.bot.get_chat_member(chat_id=update.effective_chat.id, user_id=update.effective_user.id)
            is_admin = member.status in [ChatMember.ADMINISTRATOR, ChatMember.CREATOR]
            if not is_admin and update.message:
                # Отвечаем только если есть сообщение
                await update.message.reply_text("❌ Только администраторы могут использовать эту команду.", quote=True)
            return is_admin
        except Exception as e:
            logger.error(f"Ошибка проверки админа: {e}")
            return False

    async def register_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Регистрация темы/чата для отправки ПРИВЕТСТВИЙ."""
        if not update.message or not await self.check_admin(update, context): return
        if not context.args:
            return await update.message.reply_text("❌ Укажите имя. Пример: `/registertopic Приветствие`", quote=True)
        
        name = " ".join(context.args)
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        
        self.registered_topics[name] = {"chat_id": update.message.chat.id, "thread_id": thread_id}
        self.save_data()
        
        topic_info = f"Тема **'{name}'**" if thread_id else f"Чат **'{name}'**"
        await update.message.reply_text(f"✅ {topic_info} зарегистрирован(а) для **ПРИВЕТСТВИЙ**. Теперь можно выбрать в меню.", parse_mode='Markdown', quote=True)

    async def register_monitor_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Регистрация темы/чата для АВТО-ОЧИСТКИ."""
        if not update.message or not await self.check_admin(update, context): return
        if not context.args:
            return await update.message.reply_text("❌ Укажите имя. Пример: `/monitorcleanup Флудилка`", quote=True)
        
        name = " ".join(context.args)
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        
        # Инициализируем данные для темы
        self.monitored_topics[name] = {
            "chat_id": update.message.chat.id, 
            "thread_id": thread_id,
            "cleanup_time": self.monitored_topics.get(name, {}).get('cleanup_time', "18:00"), 
            "messages": []
        }
        self.save_data()
        self.setup_schedulers() # Перепланируем, чтобы учесть новую тему
        
        topic_info = f"Тема **'{name}'**" if thread_id else f"Чат **'{name}'**"
        await update.message.reply_text(
            f"✅ {topic_info} зарегистрирован(а) для **АВТО-ОЧИСТКИ**.\n"
            f"Не-админские сообщения будут удаляться в **{self.monitored_topics[name]['cleanup_time']} UTC**.\n"
            f"Для настройки времени используйте меню.", 
            parse_mode='Markdown',
            quote=True
        )

    async def set_auto_response(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Устанавливает автоматический ответ для текущей темы."""
        if not update.message or not await self.check_admin(update, context): return
        
        chat_id = update.message.chat.id
        thread_id = update.message.message_thread_id
        
        if not context.args:
            return await update.message.reply_text(
                "❌ Укажите текст ответа. Пример:\n`/setautoresp ОК, принято!`\n"
                "Чтобы отключить: `/setautoresp off`", 
                parse_mode='Markdown',
                quote=True
            )

        key = f"{chat_id}_{thread_id}"
        response_text = " ".join(context.args)
        
        if response_text.lower() == 'off':
            if key in self.auto_response_topics:
                del self.auto_response_topics[key]
                self.save_data()
                await update.message.reply_text(f"✅ Автоматический ответ для этой темы отключен.", quote=True)
            else:
                 await update.message.reply_text(f"❌ Автоматический ответ для этой темы не был включен.", quote=True)
            return

        self.auto_response_topics[key] = response_text
        self.save_data()
        
        topic_info = f"Тема **'{update.message.chat.title}'**" if thread_id else f"Чат **'{update.message.chat.title}'**"
        await update.message.reply_text(
            f"✅ Автоматический ответ установлен для: {topic_info}.\n"
            f"Бот будет отвечать: **{response_text}**", 
            parse_mode='Markdown',
            quote=True
        )
    
    async def handle_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Сбор сообщений для очистки, фильтр запрещенных слов и авто-ответ."""
        if not update.message or not update.message.text: return
        
        chat_id = update.message.chat_id
        thread_id = update.message.message_thread_id
        user_id = update.message.from_user.id
        
        # Получаем админов (кэшировано)
        admin_ids = await self.get_admin_ids(chat_id)
        is_admin = user_id in admin_ids
        is_bot = update.message.from_user.is_bot
        
        if is_bot: return # Игнорируем сообщения от других ботов
        
        # 1. Автоматический ответ "ОК" в нужной теме (только для НЕ-админов)
        key = f"{chat_id}_{thread_id}"
        if key in self.auto_response_topics and not is_admin:
            response_text = self.auto_response_topics[key]
            try:
                # Отвечаем на сообщение пользователя в его теме
                await update.message.reply_text(response_text, quote=True)
                logger.info(f"✅ Автоматический ответ '{response_text}' отправлен в теме {thread_id}.")
            except Exception as e:
                logger.error(f"Ошибка отправки авто-ответа: {e}")

        # 2. Фильтр запрещенных слов (для всех)
        if self.forbidden_words:
            text = update.message.text.lower()
            # Проверяем на полное совпадение слова (\b)
            if any(re.search(r'\b' + re.escape(word) + r'\b', text) for word in self.forbidden_words):
                try:
                    # Тихо удаляем сообщение
                    await self.bot.delete_message(chat_id=chat_id, message_id=update.message.message_id, message_thread_id=thread_id)
                    logger.info(f"🤐 Удалено сообщение пользователя {user_id} из-за запрещенного слова.")
                    return # Прекращаем обработку после удаления
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение с запрещенным словом: {e}")

        # 3. Сбор сообщений для авто-очистки (только для НЕ-админов)
        topic_name = self.get_monitored_topic_name(chat_id, thread_id)
        if topic_name and not is_admin:
            # Сохраняем сообщение только если оно от обычного пользователя
            self.monitored_topics[topic_name]['messages'].append({
                "message_id": update.message.message_id, 
                "user_id": user_id
            })

    # -----------------------------------------------------------------
    # ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И МЕНЮ (ЛС)
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
    
    def get_monitored_topic_name(self, chat_id, thread_id):
        """Получение имени мониторируемой темы по chat_id и thread_id."""
        for name, data in self.monitored_topics.items():
            if data['chat_id'] == chat_id and data['thread_id'] == thread_id:
                return name
        return None

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда старт."""
        if update.message and update.message.chat.type == 'private':
            await self._send_main_menu(update.message.chat_id)
        elif update.message:
             await update.message.reply_text("Для управления ботом используйте личные сообщения.", quote=True)

    # --- МЕНЮ ---
    async def _send_main_menu(self, chat_id):
        """Отправка нового сообщения Главного меню."""
        keyboard = [
            [InlineKeyboardButton("📅 Ежедневные Приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("🧹 Авто-очистка тем (Worker)", callback_data="monitored_topics_menu")],
            [InlineKeyboardButton("🤐 Запрещенные Слова", callback_data="forbidden_words_menu")],
            [InlineKeyboardButton("⏰ Настройка времени (UTC)", callback_data="timers")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.bot.send_message(chat_id, "👋 **Главное меню:**", reply_markup=reply_markup, parse_mode='Markdown')

    async def _edit_main_menu(self, query):
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
    
    # --- МЕНЮ ТАЙМЕРОВ (Общее) ---
    async def _edit_timers_menu(self, query):
        """Меню настройки общего времени."""
        
        keyboard = [
            [InlineKeyboardButton(f"🕐 Отправка Приветствия: {self.welcome_time} UTC", callback_data="timer_welcome")],
            [InlineKeyboardButton(f"🗑️ Удаление Приветствия: {self.welcome_delete_time} UTC", callback_data="timer_welcome_delete")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "⏰ **Настройка времени (UTC)**\n\n"
            "Установите время для **ежедневной отправки** и **удаления** приветственных сообщений.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    # --- МЕНЮ ЗАПРЕЩЕННЫХ СЛОВ ---
    async def _edit_forbidden_words_menu(self, query):
        """Меню настройки запрещенных слов."""
        
        words_count = len(self.forbidden_words)
        words_list = ", ".join(self.forbidden_words[:5])
        if words_count > 5: words_list += f", и еще {words_count - 5}..."

        keyboard = [
            [InlineKeyboardButton(f"📝 Изменить список ({words_count} слов)", callback_data="set_forbidden_words")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🤐 **Запрещенные Слова**\n\n"
            f"Сообщения, содержащие любое из этих слов, будут **немедленно и бесшумно удалены**.\n\n"
            f"**Текущий список:**\n{words_list if words_count > 0 else '*Список пуст.*'}",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    # --- МЕНЮ АВТО-ОЧИСТКИ ---
    
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
            "Выберите тему, чтобы изменить время ежедневной очистки (удаляются только сообщения пользователей, не админов).",
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
            f"Текущее время ежедневной очистки установлено на **{current_time} UTC**.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
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
        
    # --- МЕНЮ ЕЖЕДНЕВНЫХ ПРИВЕТСТВИЙ ---

    async def _edit_daily_messages_menu(self, query):
        """Меню для настройки ежедневных приветствий (на всю неделю)."""
        target_name = self.get_current_target_name() or "❌ Не задана"
        # ЭТА КНОПКА ВОССТАНОВЛЕНА
        status = "Включено ✅" if self.welcome_mode and self.target_chat_id and self.daily_messages else "Выключено ❌"
        
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

        keyboard.append([InlineKeyboardButton(f"🎯 Целевая тема: {target_name}", callback_data="set_target_topic")])
        keyboard.append([InlineKeyboardButton(f"▶️ Статус: {status}", callback_data="toggle_welcome_mode")])
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "📅 **Ежедневные Приветствия**\n\n"
            f"**Статус системы:** {status}\n"
            f"Сообщения отправляются в **{self.welcome_time} UTC** и удаляются в **{self.welcome_delete_time} UTC**.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def _send_daily_messages_menu(self, chat_id):
        """Отправка НОВОГО сообщения меню ежедневных приветствий (после ввода текста)."""
        target_name = self.get_current_target_name() or "❌ Не задана"
        status = "Включено ✅" if self.welcome_mode and self.target_chat_id and self.daily_messages else "Выключено ❌"
        
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

        keyboard.append([InlineKeyboardButton(f"🎯 Целевая тема: {target_name}", callback_data="set_target_topic")])
        keyboard.append([InlineKeyboardButton(f"▶️ Статус: {status}", callback_data="toggle_welcome_mode")])
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.bot.send_message(
            chat_id,
            "📅 **Ежедневные Приветствия**\n\n"
            f"**Статус системы:** {status}\n"
            f"Сообщения отправляются в **{self.welcome_time} UTC** и удаляются в **{self.welcome_delete_time} UTC**.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def _edit_daily_message_day_menu(self, query, day_index):
        """Меню для настройки сообщения на конкретный день."""
        day_name = self.get_day_name(day_index)
        current_message = self.daily_messages.get(str(day_index), "*Сообщение не задано.*")
        
        keyboard = [
            [InlineKeyboardButton("📝 Задать/Изменить текст", callback_data=f"set_message_{day_index}")],
        ]
        if str(day_index) in self.daily_messages:
             keyboard.append([InlineKeyboardButton("🗑️ Удалить сообщение", callback_data=f"delete_message_{day_index}")])

        keyboard.append([InlineKeyboardButton("🔙 Назад к дням", callback_data="back_daily")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        display_message = current_message
        if len(current_message) > 200:
            display_message = current_message[:200] + "..."

        await query.edit_message_text(
            f"📅 **Сообщение для {day_name}**\n\n"
            f"**Текущий текст:**\n"
            f"```\n{display_message}\n```\n"
            f"Используйте разметку Markdown для форматирования.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def _edit_target_topic_menu(self, query):
        """Меню выбора целевой темы для приветствий."""
        if not self.registered_topics:
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_daily")]]
            return await query.edit_message_text(
                "❌ **Нет зарегистрированных тем.**\n\n"
                "Чтобы добавить тему, используйте команду `/registertopic [ИМЯ ТЕМЫ]` в нужной теме в вашей группе.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        
        keyboard = []
        for name, data in self.registered_topics.items():
            status = "✅" if self.target_chat_id == data['chat_id'] and self.target_thread_id == data['thread_id'] else " "
            keyboard.append([InlineKeyboardButton(f"{status} {name}", callback_data=f"select_topic:{name}")])

        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_daily")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text("🎯 **Выберите, куда отправлять приветствия:**", reply_markup=reply_markup)

    # -----------------------------------------------------------------
    # ГЕНЕРАЛЬНЫЙ CALLBACK-ОБРАБОТЧИК (ОБРАБОТКА ВСЕХ НАЖАТИЙ)
    # -----------------------------------------------------------------

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        
        # Очистка состояния ввода при нажатии навигационных кнопок
        if data in ["back_main", "daily_messages", "back_monitor", "timers", "forbidden_words_menu"]:
            context.user_data.pop('next_action', None)
            context.user_data.pop('day_index', None)
            context.user_data.pop('monitor_topic_name', None)

        # --- НАВИГАЦИЯ ---
        if data == "back_main": return await self._edit_main_menu(query)
        elif data == "daily_messages" or data == "back_daily": return await self._edit_daily_messages_menu(query)
        elif data == "monitored_topics_menu" or data == "back_monitor": return await self._edit_monitored_topics_menu(query)
        elif data == "timers": return await self._edit_timers_menu(query)
        elif data == "forbidden_words_menu": return await self._edit_forbidden_words_menu(query)
        
        # --- МЕНЮ ПРИВЕТСТВИЙ ---
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
                f"Отправьте мне сообщение. Используйте **Markdown** для форматирования. Для отмены нажмите /start.",
                parse_mode='Markdown'
            )
        
        elif data.startswith("delete_message_"):
            day_index = str(data.split("_")[-1])
            day_name = self.get_day_name(int(day_index))
            if day_index in self.daily_messages:
                del self.daily_messages[day_index]
                self.save_data()
                self.setup_schedulers()
                await query.answer(f"🗑️ Сообщение для {day_name} удалено!", show_alert=True)
            await self._edit_daily_message_day_menu(query, int(day_index))
        
        elif data == "toggle_welcome_mode":
            self.welcome_mode = not self.welcome_mode
            self.save_data()
            self.setup_schedulers()
            await query.answer(f"Приветствия: {'Включены' if self.welcome_mode else 'Выключены'}")
            return await self._edit_daily_messages_menu(query)

        elif data == "set_target_topic":
            return await self._edit_target_topic_menu(query) 

        elif data.startswith("select_topic:"):
            topic_name = data.split(":")[1]
            data = self.registered_topics.get(topic_name)
            if data:
                self.target_chat_id = data['chat_id']
                self.target_thread_id = data['thread_id']
                self.save_data()
                self.setup_schedulers()
                await query.answer(f"✅ Целевая тема '{topic_name}' установлена!", show_alert=True)
            else:
                await query.answer("❌ Ошибка: Тема не найдена.", show_alert=True)
            return await self._edit_daily_messages_menu(query)
        
        # --- МЕНЮ ТАЙМЕРОВ (Общее) ---
        elif data.startswith("timer_"):
            timer_key_map = {
                'welcome': 'welcome_time',
                'welcome_delete': 'welcome_delete_time',
            }
            timer_key_name = data.split("_")[-1]
            timer_key = timer_key_map.get(timer_key_name)
            
            if timer_key:
                context.user_data['next_action'] = INPUT_STATE_TIME
                context.user_data['timer_key'] = timer_key
                await query.edit_message_text(
                    "⏰ **Введите новое время в формате ЧЧ:ММ (UTC).**\n\n"
                    f"Текущее: {getattr(self, timer_key, 'N/A')}. Например: `09:30`.",
                    parse_mode='Markdown'
                )

        # --- МЕНЮ АВТО-ОЧИСТКИ ---
        elif data.startswith("select_monitor_"):
            topic_name = data.split("_")[-1]
            return await self._edit_cleanup_time_menu(query, topic_name)
        
        elif data.startswith("set_cleanup_time_"):
            topic_name = data.split("_")[-1]
            current_time = self.monitored_topics.get(topic_name, {}).get('cleanup_time', '18:00')
            
            context.user_data['next_action'] = INPUT_STATE_CLEANUP_TIME
            context.user_data['monitor_topic_name'] = topic_name
            
            await query.edit_message_text(
                f"⏰ **Введите новое время очистки для темы '{topic_name}' в формате ЧЧ:ММ (UTC).**\n\n"
                f"Текущее: {current_time}. Например: `19:30`.",
                parse_mode='Markdown'
            )
        
        elif data.startswith("delete_monitor_"):
            topic_name = data.split("_")[-1]
            
            if topic_name in self.monitored_topics:
                del self.monitored_topics[topic_name]
                self.save_data()
                self.setup_schedulers() 
                await query.answer(f"🗑️ Тема '{topic_name}' удалена из авто-очистки.", show_alert=True)
            
            return await self._edit_monitored_topics_menu(query)
            
        # --- МЕНЮ ЗАПРЕЩЕННЫХ СЛОВ ---
        elif data == "set_forbidden_words":
            context.user_data['next_action'] = INPUT_STATE_FORBIDDEN_WORDS
            await query.edit_message_text(
                "📝 **Введите список запрещенных слов.**\n\n"
                "Слова должны быть разделены **запятой** или **новой строкой**.\n\n"
                f"Текущий список: {', '.join(self.forbidden_words)}",
                parse_mode='Markdown'
            )

    # -----------------------------------------------------------------
    # ОБРАБОТЧИК ТЕКСТОВОГО ВВОДА (ЛС)
    # -----------------------------------------------------------------

    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or update.message.chat.type != 'private': return
        if 'next_action' not in context.user_data: return 
        
        user_input = update.message.text.strip()
        action = context.user_data.pop('next_action')
        chat_id = update.message.chat_id
        
        # --- Ввод запрещенных слов ---
        if action == INPUT_STATE_FORBIDDEN_WORDS:
            # Разделяем по запятым и/или переносам строк, фильтруем пустые
            words_list = [w.strip().lower() for w in re.split(r'[,\n\r]+', user_input) if w.strip()]
            self.forbidden_words = words_list
            self.save_data()
            
            await self.bot.send_message(chat_id, f"✅ **Список запрещенных слов обновлен!** Всего слов: {len(words_list)}.", parse_mode='Markdown')
            # Отправка временного сообщения для редактирования
            temp_msg = await self.bot.send_message(chat_id, "Возврат в меню...")
            return await self._edit_forbidden_words_menu(temp_msg.edit_text_obj)

        # --- Ввод ежедневного сообщения ---
        elif action == INPUT_STATE_DAILY_MESSAGE:
            day_index = context.user_data.pop('day_index')
            day_name = self.get_day_name(day_index)
            
            self.daily_messages[str(day_index)] = user_input
            self.save_data()
            self.setup_schedulers()
            
            await self.bot.send_message(chat_id, f"✅ **Сообщение для {day_name} успешно сохранено!**", parse_mode='Markdown')
            return await self._send_daily_messages_menu(chat_id)

        # --- Ввод времени ---
        elif action in [INPUT_STATE_TIME, INPUT_STATE_CLEANUP_TIME]:
            
            if not re.fullmatch(r'\d{2}:\d{2}', user_input):
                await update.message.reply_text("❌ Неверный формат. Используйте **ЧЧ:ММ** (например, `09:30`).")
                context.user_data['next_action'] = action 
                return

            try:
                time.fromisoformat(user_input)
                
                if action == INPUT_STATE_TIME:
                    timer_key = context.user_data.pop('timer_key')
                    setattr(self, timer_key, user_input)
                    self.save_data()
                    self.setup_schedulers()
                    await self.bot.send_message(chat_id, f"✅ **Время для `{timer_key.replace('_', ' ').replace('welcome', 'приветствия')}` обновлено на {user_input} UTC.**", parse_mode='Markdown')
                    # Отправка временного сообщения для редактирования
                    temp_msg = await self.bot.send_message(chat_id, "Возврат в меню времени...")
                    return await self._edit_timers_menu(temp_msg.edit_text_obj) 

                elif action == INPUT_STATE_CLEANUP_TIME:
                    topic_name = context.user_data.pop('monitor_topic_name')
                    if topic_name in self.monitored_topics:
                        self.monitored_topics[topic_name]['cleanup_time'] = user_input
                        self.save_data()
                        self.setup_schedulers()
                        await self.bot.send_message(chat_id, f"✅ **Время очистки для '{topic_name}' обновлено на {user_input} UTC.**", parse_mode='Markdown')
                        return await self._edit_monitored_topics_menu_after_input(chat_id)
                    else:
                        await update.message.reply_text("❌ Ошибка: Тема мониторинга не найдена.")

            except ValueError:
                await update.message.reply_text("❌ Некорректное время. Проверьте ЧЧ (00-23) и ММ (00-59).")
                context.user_data['next_action'] = action
    
    # -----------------------------------------------------------------
    # ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА
    # -----------------------------------------------------------------
    def run(self):
        """Запуск бота."""
        # Создание application с хуком для запуска планировщика
        application = Application.builder().token(BOT_TOKEN).post_init(self.post_init_hook).build()
        
        # 1. Команды (группа/ЛС)
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("registertopic", self.register_topic))
        application.add_handler(CommandHandler("monitorcleanup", self.register_monitor_topic))
        application.add_handler(CommandHandler("setautoresp", self.set_auto_response))

        # 2. Обработчик нажатий кнопок (ЛС)
        application.add_handler(CallbackQueryHandler(self.handle_callback_query))

        # 3. Обработчик текстовых сообщений (для ввода данных в меню в ЛС)
        application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, self.handle_text_input))
        
        # 4. Обработчик всех сообщений в группе (для сбора и фильтрации)
        application.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, self.handle_group_message))

        logger.info("🤖 Бот запущен (Polling)...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

# Код, который запускает класс
if __name__ == '__main__':
    if BOT_TOKEN:
        bot_instance = DailyMessageBot(Application.builder().token(BOT_TOKEN).build())
        bot_instance.run()
    else:
        pass
