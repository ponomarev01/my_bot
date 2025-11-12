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
# ЗАМЕНИТЕ ЭТОТ ТОКЕН НА ВАШ РЕАЛЬНЫЙ
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ") 

if BOT_TOKEN == "ВАШ_ТОКЕН_ЗДЕСЬ":
    logger.error("🚫 BOT_TOKEN не установлен. Пожалуйста, замените 'ВАШ_ТОКЕН_ЗДЕСЬ' на реальный токен.")
    # sys.exit(1)
# -----------------------------------------------------------------------------

# Константы для состояний ввода (для более сложной логики, пока не используются)
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
        self.registered_topics: Dict[str, Dict[str, Any]] = {} 
        self.target_chat_id: Optional[int] = None 
        self.target_thread_id: Optional[int] = None 
        self.last_welcome_message: Dict[str, int] = {} 
        
        # Настройки авто-очистки
        self.bot_id: Optional[int] = None 
        self.monitored_topics: Dict[str, Dict[str, Any]] = {} 
        
        # Настройки запрещенных слов
        self.forbidden_words: list = [] 
        
        # Настройки авто-ответа "ОК"
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
        
    # --- СОХРАНЕНИЕ / ЗАГРУЗКА ДАННЫХ ---
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
                        # При загрузке очищаем список сообщений
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
                # Удаляем временные данные (сообщения) перед сохранением
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

    # -----------------------------------------------------------------
    # ПЛАНИРОВЩИКИ (Async) - Реализованы полностью
    # -----------------------------------------------------------------
    def setup_schedulers(self):
        """Настройка всех задач по расписанию."""
        
        for job in self.scheduler.get_jobs():
            try:
                self.scheduler.remove_job(job.id)
            except Exception:
                pass 

        # 1. Приветствие и удаление
        has_messages = bool(self.daily_messages)
        is_target_set = bool(self.target_chat_id)

        if self.welcome_mode and has_messages and is_target_set:
            try:
                h, m = map(int, self.welcome_time.split(':'))
                self.scheduler.add_job(self.send_welcome_message_job, CronTrigger(hour=h, minute=m, timezone=pytz.UTC), id='welcome_message', replace_existing=True)
                
                h_del, m_del = map(int, self.welcome_delete_time.split(':'))
                self.scheduler.add_job(self.delete_welcome_message_job, CronTrigger(hour=h_del, minute=m_del, timezone=pytz.UTC), id='welcome_delete', replace_existing=True)
            except Exception as e: logger.error(f"Ошибка планирования приветствий: {e}")
        
        # 2. Очистка мониторируемых тем
        for topic_name, topic_data in self.monitored_topics.items():
            job_id = f'cleanup_{topic_name}'
            try:
                cleanup_time = topic_data.get('cleanup_time', '18:00')
                h, m = map(int, cleanup_time.split(':'))
                self.scheduler.add_job(self.cleanup_topic_job, CronTrigger(hour=h, minute=m, timezone=pytz.UTC), args=[topic_name], id=job_id, replace_existing=True)
            except Exception as e: logger.error(f"Ошибка планирования очистки ({topic_name}): {e}")

    async def send_welcome_message_job(self):
        """Отправка ежедневного приветствия."""
        try:
            today = datetime.now(pytz.UTC).weekday() 
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

    async def cleanup_topic_job(self, topic_name: str):
        """Очистка сообщений не-админов и сообщений бота в отслеживаемой теме."""
        logger.info(f"🧹 Запуск очистки для темы: {topic_name}")
        if topic_name not in self.monitored_topics: return
            
        topic_data = self.monitored_topics[topic_name]
        chat_id = topic_data['chat_id']
        messages_to_delete = topic_data.get('messages', [])
        
        if not messages_to_delete: 
            logger.info(f"Очистка: Нет сообщений для удаления в {topic_name}.")
            return

        admin_ids = await self.get_admin_ids(chat_id)
        if not admin_ids: 
            logger.warning(f"Не удалось получить список админов для {topic_name}. Очистка отложена.")
            return

        deleted_count = 0
        
        for msg in messages_to_delete:
            user_id = msg['user_id']
            is_non_admin = user_id not in admin_ids
            is_bot_message = user_id == self.bot_id
            
            if is_non_admin or is_bot_message:
                try:
                    await self.bot.delete_message(
                        chat_id=chat_id, 
                        message_id=msg['message_id'], 
                        message_thread_id=topic_data.get('thread_id')
                    )
                    deleted_count += 1
                except Exception as e: 
                    logger.debug(f"Не удалось удалить сообщение {msg['message_id']} в {chat_id}: {e}") 
        
        logger.info(f"✅ Очистка {topic_name} завершена. Удалено {deleted_count} сообщений.")
        
        self.monitored_topics[topic_name]['messages'] = []
        await self._save_data_async() 
    
    # -----------------------------------------------------------------
    # ПРАВА И АДМИНЫ
    # -----------------------------------------------------------------

    async def get_admin_ids(self, chat_id: int) -> List[int]:
        """Кэширование и получение ID администраторов."""
        now = datetime.now()
        cache_data = self.admin_cache.get(chat_id)
        
        if cache_data and (now - cache_data.get('timestamp', now)).total_seconds() < 600:
            return cache_data['ids']
        try:
            admins = await self.bot.get_chat_administrators(chat_id)
            admin_ids = [admin.user.id for admin in admins]
            self.admin_cache[chat_id] = {'ids': admin_ids, 'timestamp': now}
            return admin_ids
        except Exception as e:
            logger.error(f"Не удалось получить список админов для чата {chat_id}: {e}")
            return []

    async def check_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Проверка прав администратора."""
        if not update.effective_user: return False
        if update.effective_chat.type == 'private': return True 

        try:
            member = await context.bot.get_chat_member(chat_id=update.effective_chat.id, user_id=update.effective_user.id)
            is_admin = member.status in [ChatMember.ADMINISTRATOR, ChatMember.CREATOR]
            if not is_admin and update.message:
                await update.message.reply_text("❌ Только администраторы могут использовать эту команду.", quote=True)
            return is_admin
        except Exception as e:
            logger.error(f"Ошибка проверки админа: {e}")
            return False

    # -----------------------------------------------------------------
    # ОБРАБОТЧИКИ КОМАНД (ГРУППА)
    # -----------------------------------------------------------------
    
    async def register_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Регистрация темы/чата для отправки ПРИВЕТСТВИЙ."""
        
        if not update.message: return
        if not await self.check_admin(update, context): return
        
        if not context.args:
            return await update.message.reply_text("❌ Укажите имя. Пример: `/registertopic Приветствие`", quote=True)
        
        name = " ".join(context.args)
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        
        self.registered_topics[name] = {"chat_id": update.message.chat.id, "thread_id": thread_id}
        self.save_data()
        
        topic_info = f"Тема **'{name}'**" if thread_id else f"Чат **'{name}'**"
        await update.message.reply_text(f"✅ {topic_info} зарегистрирован(а) для **ПРИВЕТСТВИЙ**. Теперь можно выбрать в меню.", parse_mode='Markdown', quote=True)
        logger.info(f"УСПЕХ: Тема '{name}' зарегистрирована.")


    async def register_monitor_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Регистрация темы/чата для АВТО-ОЧИСТКИ."""
        if not update.message or not await self.check_admin(update, context): return
        if not context.args:
            return await update.message.reply_text("❌ Укажите имя. Пример: `/monitorcleanup Флудилка`", quote=True)
        
        name = " ".join(context.args)
        thread_id = update.message.message_thread_id if update.message.is_topic_message else None
        
        self.monitored_topics[name] = {
            "chat_id": update.message.chat.id, 
            "thread_id": thread_id,
            "cleanup_time": self.monitored_topics.get(name, {}).get('cleanup_time', "18:00"), 
            "messages": []
        }
        self.save_data()
        self.setup_schedulers()
        
        topic_info = f"Тема **'{name}'**" if thread_id else f"Чат **'{name}'**"
        await update.message.reply_text(
            f"✅ {topic_info} зарегистрирован(а) для **АВТО-ОЧИСТКИ**.\n"
            f"Не-админские сообщения и сообщения бота будут удаляться в **{self.monitored_topics[name]['cleanup_time']} UTC**.\n"
            f"Для настройки времени используйте меню в ЛС.", 
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

        key_thread_id = thread_id if thread_id else 0 
        key = f"{chat_id}_{key_thread_id}"
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
        
        chat_title = update.message.chat.title if update.message.chat.title else "Этот чат"
        topic_info = f"Тема **'{chat_title}'**" if thread_id else f"Чат **'{chat_title}'**"
        await update.message.reply_text(
            f"✅ Автоматический ответ установлен для: {topic_info}.\n"
            f"Бот будет отвечать: **{response_text}**", 
            parse_mode='Markdown',
            quote=True
        )
        
    async def get_monitored_topic_name(self, chat_id: int, thread_id: Optional[int]) -> Optional[str]:
        """Получение имени мониторируемой темы по chat_id и thread_id."""
        for name, data in self.monitored_topics.items():
            if data.get('chat_id') == chat_id and data.get('thread_id') == thread_id:
                return name
        return None

    async def handle_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Сбор сообщений для очистки, фильтр запрещенных слов и авто-ответ."""
        if not update.message or not update.message.text or update.message.chat.type not in ['group', 'supergroup']: 
            return
        
        chat_id = update.message.chat_id
        thread_id = update.message.message_thread_id
        user_id = update.message.from_user.id
        
        admin_ids = await self.get_admin_ids(chat_id)
        is_admin = user_id in admin_ids
        is_bot = update.message.from_user.is_bot
        
        # 1. Автоматический ответ "ОК"
        key_thread_id = thread_id if thread_id else 0 
        key = f"{chat_id}_{key_thread_id}"
        
        if key in self.auto_response_topics and not is_admin and not is_bot:
            response_text = self.auto_response_topics[key]
            try:
                sent_message = await update.message.reply_text(response_text, quote=True)
                
                topic_name = await self.get_monitored_topic_name(chat_id, thread_id)
                if topic_name and self.bot_id:
                    self.monitored_topics[topic_name]['messages'].append({
                        "message_id": sent_message.message_id, 
                        "user_id": self.bot_id
                    })
                    self.save_data()
            except Exception as e:
                logger.error(f"Ошибка отправки авто-ответа: {e}")

        # 2. Фильтр запрещенных слов
        if self.forbidden_words and not is_bot:
            text = update.message.text.lower()
            if any(re.search(r'\b' + re.escape(word.lower()) + r'\b', text) for word in self.forbidden_words):
                try:
                    await self.bot.delete_message(
                        chat_id=chat_id, 
                        message_id=update.message.message_id, 
                        message_thread_id=thread_id
                    )
                    return
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение с запрещенным словом: {e}")

        # 3. Сбор сообщений для авто-очистки
        topic_name = await self.get_monitored_topic_name(chat_id, thread_id)
        if topic_name and not is_admin and not is_bot:
            self.monitored_topics[topic_name]['messages'].append({
                "message_id": update.message.message_id, 
                "user_id": user_id
            })
            self.save_data()

    # -----------------------------------------------------------------
    # ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И МЕНЮ (ЛС) - Реализованы полностью
    # -----------------------------------------------------------------
    
    def get_day_name(self, index: int) -> str:
        """Возвращает название дня недели по индексу (0=Пн, 6=Вс)."""
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        return days[index]

    def get_current_target_name(self) -> Optional[str]:
        """Получение имени целевой темы для приветствий."""
        if self.target_chat_id is None: return None
        for name, data in self.registered_topics.items():
            if self.target_chat_id == data.get('chat_id') and self.target_thread_id == data.get('thread_id'):
                return name
        return None 
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда старт."""
        if update.message and update.message.chat.type == 'private':
            keys_to_pop = [
                'next_action', 'day_index', 'monitor_topic_name', 
                'timer_key', 'return_to_daily_menu', 'forbidden_words_input'
            ]
            for key in keys_to_pop:
                context.user_data.pop(key, None) 
                
            await self._send_main_menu(update.message.chat_id)
        elif update.message:
            await update.message.reply_text("Для управления ботом используйте личные сообщения.", quote=True)

    # --- ОБРАБОТКА ВСЕХ КНОПОК ---
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка всех inline-кнопок."""
        query = update.callback_query
        await query.answer() 
        data = query.data
        
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
        
        # ЗАГЛУШКИ ДЛЯ НЕОБХОДИМЫХ КНОПОК
        elif data.startswith("select_day_"):
            await query.edit_message_text("🚧 Ввод сообщения для дня недели (select_day)")
        elif data == "timer_welcome":
            await query.edit_message_text("🚧 Настройка времени отправки приветствий (timer_welcome)")
        elif data == "timer_welcome_delete":
            await query.edit_message_text("🚧 Настройка времени удаления приветствий (timer_welcome_delete)")
        elif data == "toggle_welcome_mode":
            await query.edit_message_text("🚧 Переключение режима приветствий (toggle_welcome_mode)")
        elif data == "forbidden_words_menu":
            await query.edit_message_text("🚧 Настройка запрещенных слов (forbidden_words_menu)")
        elif data == "timers":
            await query.edit_message_text("🚧 Общее меню таймеров (timers)")
        
        else:
             await query.edit_message_text(f"🚧 Раздел в разработке (Callback: {data})") 
             
    # --- МЕТОДЫ ДЕЙСТВИЙ ---

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
             
    # --- МЕТОДЫ МЕНЮ ---
    
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
            await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
        except Exception:
            pass 
            
    async def _edit_monitored_topics_menu(self, query: Update.callback_query):
        """Меню выбора темы для настройки времени очистки (заглушка)."""
        keyboard = [[InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]]
        await query.edit_message_text(
            "🧹 **Меню Авто-очистки**\n\n"
            "🚧 Этот раздел требует дальнейшей реализации (список тем, кнопки настройки времени).",
            reply_markup=InlineKeyboardMarkup(keyboard),
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
            pass


# -----------------------------------------------------------------
# ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА
# -----------------------------------------------------------------

def main() -> None:
    """Запуск бота."""
    if BOT_TOKEN == "ВАШ_ТОКЕН_ЗДЕСЬ":
         logger.error("🚫 Останавливаю запуск: токен не установлен.")
         return

    application = Application.builder().token(BOT_TOKEN).post_init(DailyMessageBot.post_init_hook).build()
    bot_instance = DailyMessageBot(application)

    application.post_init = bot_instance.post_init_hook

    # 2. Обработчики команд
    application.add_handler(CommandHandler("start", bot_instance.start))
    application.add_handler(CommandHandler("registertopic", bot_instance.register_topic))
    application.add_handler(CommandHandler("monitorcleanup", bot_instance.register_monitor_topic))
    application.add_handler(CommandHandler("setautoresp", bot_instance.set_auto_response))

    # 3. Обработчик всех сообщений группы
    group_filter = filters.ChatType.GROUPS & filters.TEXT
    application.add_handler(MessageHandler(group_filter, bot_instance.handle_group_message))

    # 4. Обработчик кнопок (CallbackQueryHandler)
    application.add_handler(CallbackQueryHandler(bot_instance.handle_callback_query))

    logger.info("🚀 Бот запущен в режиме polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
