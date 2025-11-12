import logging
import json
import os
from datetime import datetime, time
import asyncio

# --- ИМПОРТЫ ДЛЯ PTB v20 (Современная версия) ---
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
# ⚠️ ЗАМЕНИТЕ ЭТОТ ТОКЕН НА СВОЙ! 
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE" 
# -----------------------------------------------------------------------------

class DailyMessageBot:
    def __init__(self, application: Application):
        self.application = application
        self.bot = application.bot
        self.data_file = "bot_data.json"
        
        # Настройки режимов и времени
        self.silent_mode = False
        self.silent_start_time = "18:30"
        self.silent_end_time = "08:00"
        self.welcome_mode = True
        self.welcome_time = "09:00"
        self.welcome_delete_time = "10:00"
        
        # Хранилища
        self.daily_messages = {}
        self.registered_topics = {} 
        self.target_chat_id = None  
        self.target_thread_id = None 
        self.last_welcome_message = {} 
        self.monitored_topics = {} 
        self.forbidden_words = []
        
        self.admin_cache = {}
        self.scheduler = AsyncIOScheduler(timezone=pytz.UTC)
        self.load_data()
        self.setup_schedulers()
        
    def load_data(self):
        """Загрузка данных из файла"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Загрузка всех настроек
                    for key, default in [
                        ('silent_mode', False), ('silent_start_time', "18:30"), ('silent_end_time', "08:00"),
                        ('welcome_mode', True), ('welcome_time', "09:00"), ('welcome_delete_time', "10:00"),
                        ('daily_messages', {}), ('registered_topics', {}), ('target_chat_id', None),
                        ('target_thread_id', None), ('last_welcome_message', {}), ('monitored_topics', {}),
                        ('forbidden_words', [])
                    ]:
                        setattr(self, key, data.get(key, default))
                    
                    # Инициализация списка сообщений для мониторинга
                    for name in self.monitored_topics:
                        if 'messages' not in self.monitored_topics[name]:
                            self.monitored_topics[name]['messages'] = []

        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
    
    def save_data(self):
        """Сохранение данных в файл (асинхронно, чтобы не блокировать бота)"""
        try:
            # Используем to_thread для выполнения блокирующей операции в отдельном потоке
            asyncio.run_coroutine_threadsafe(self._save_data_async(), self.application.loop)
        except Exception as e:
            logger.error(f"Ошибка инициирования сохранения данных: {e}")

    async def _save_data_async(self):
        """Асинхронное сохранение данных"""
        try:
            # Очистка сообщений перед сохранением (не храним их на диске)
            monitored_topics_to_save = {}
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
        """Настройка и запуск всех задач по расписанию."""
        self.schedule_welcome_message()
        self.schedule_welcome_delete()
        self.schedule_monitored_cleanup()
        if not self.scheduler.running:
            try:
                self.scheduler.start()
            except Exception as e:
                logger.warning(f"Планировщик уже запущен или ошибка: {e}")

    def schedule_welcome_message(self):
        """Планирование ежедневного приветствия."""
        try: self.scheduler.remove_job('welcome_message')
        except: pass
        try:
            hour, minute = map(int, self.welcome_time.split(':'))
            self.scheduler.add_job(self.send_welcome_message_job, CronTrigger(hour=hour, minute=minute, timezone=pytz.UTC), id='welcome_message')
            logger.info(f"✅ Приветствие: {self.welcome_time} UTC")
        except Exception as e: logger.error(f"Ошибка schedule_welcome_message: {e}")

    def schedule_welcome_delete(self):
        """Планирование удаления приветствия."""
        try: self.scheduler.remove_job('welcome_delete')
        except: pass
        try:
            hour, minute = map(int, self.welcome_delete_time.split(':'))
            self.scheduler.add_job(self.delete_welcome_message_job, CronTrigger(hour=hour, minute=minute, timezone=pytz.UTC), id='welcome_delete')
            logger.info(f"✅ Удаление приветствия: {self.welcome_delete_time} UTC")
        except Exception as e: logger.error(f"Ошибка schedule_welcome_delete: {e}")

    def schedule_monitored_cleanup(self):
        """Планирование очистки для всех отслеживаемых тем."""
        for topic_name, topic_data in self.monitored_topics.items():
            job_id = f'cleanup_{topic_name}'
            try: self.scheduler.remove_job(job_id)
            except: pass
            try:
                hour, minute = map(int, topic_data.get('cleanup_time', '18:00').split(':'))
                self.scheduler.add_job(self.cleanup_topic_job, CronTrigger(hour=hour, minute=minute, timezone=pytz.UTC), args=[topic_name], id=job_id)
                logger.info(f"✅ Очистка '{topic_name}': {topic_data.get('cleanup_time', '18:00')} UTC")
            except Exception as e: logger.error(f"Ошибка schedule_monitored_cleanup ({topic_name}): {e}")

    # -----------------------------------------------------------------
    # ЗАДАЧИ ПЛАНИРОВЩИКА (JOBS - Async)
    # -----------------------------------------------------------------
    async def send_welcome_message_job(self):
        """Отправка ежедневного приветствия."""
        try:
            today = datetime.now(pytz.UTC).weekday()
            message = self.daily_messages.get(str(today))
            if not self.welcome_mode or not message or not self.target_chat_id:
                return
            
            sent_message = await self.bot.send_message(chat_id=self.target_chat_id, text=message, message_thread_id=self.target_thread_id)
            self.last_welcome_message = {"chat_id": sent_message.chat_id, "message_id": sent_message.message_id}
            await self._save_data_async()
        except Exception as e: logger.error(f"Ошибка send_welcome_message_job: {e}")

    async def delete_welcome_message_job(self):
        """Удаление последнего приветственного сообщения."""
        if not self.last_welcome_message: return
        try:
            await self.bot.delete_message(chat_id=self.last_welcome_message['chat_id'], message_id=self.last_welcome_message['message_id'])
        except Exception as e: logger.warning(f"Не удалось удалить приветствие: {e}")
        finally:
            self.last_welcome_message = {}
            await self._save_data_async()

    async def get_admin_ids(self, chat_id):
        """Кэширование и получение ID администраторов."""
        now = datetime.now()
        # Кэш на 10 минут
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
        messages_to_delete = topic_data['messages']
        if not messages_to_delete: return

        admin_ids = await self.get_admin_ids(chat_id)
        if not admin_ids: return

        deleted_count = 0
        for msg in messages_to_delete:
            if msg['user_id'] not in admin_ids:
                try:
                    await self.bot.delete_message(chat_id=chat_id, message_id=msg['message_id'])
                    deleted_count += 1
                except Exception: pass
        
        logger.info(f"✅ Очистка {topic_name} завершена. Удалено {deleted_count} сообщений.")
        self.monitored_topics[topic_name]['messages'] = []
        await self._save_data_async()

    # -----------------------------------------------------------------
    # ОБРАБОТЧИКИ СООБЩЕНИЙ В ГРУППЕ (Async)
    # -----------------------------------------------------------------
    def is_silent_time(self):
        """Проверка, активно ли сейчас время тишины."""
        if not self.silent_mode: return False
        now = datetime.now(pytz.UTC).time()
        
        try:
            start_time_dt = time.fromisoformat(self.silent_start_time)
            end_time_dt = time.fromisoformat(self.silent_end_time)
        except ValueError:
            logger.error("Неверный формат времени тишины.")
            return False

        if start_time_dt < end_time_dt:
            # Тихий час в пределах одного дня 
            return start_time_dt <= now <= end_time_dt
        else:
            # Тихий час переходит через полночь 
            return now >= start_time_dt or now <= end_time_dt

    async def check_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Проверка, является ли пользователь администратором."""
        if update.message.chat.type == 'private': return True
        try:
            member = await context.bot.get_chat_member(chat_id=update.message.chat.id, user_id=update.message.from_user.id)
            is_admin = member.status in [ChatMember.ADMINISTRATOR, ChatMember.CREATOR]
            if not is_admin:
                await update.message.reply_text("❌ Только администраторы.")
            return is_admin
        except Exception as e:
            logger.error(f"Ошибка проверки админа: {e}")
            return False

    async def register_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для регистрации темы (потока) для приветствий."""
        if not await self.check_admin(update, context): return
        if not context.args:
            await update.message.reply_text("❌ Укажите имя.\nПример: `/registertopic Новости`")
            return
        
        name = " ".join(context.args)
        self.registered_topics[name] = {"chat_id": update.message.chat.id, "thread_id": update.message.message_thread_id}
        self.save_data()
        await update.message.reply_text(f"✅ Тема для ПРИВЕТСТВИЙ '{name}' зарегистрирована.")

    async def register_monitor_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда для регистрации темы (потока) для авто-очистки."""
        if not await self.check_admin(update, context): return
        if not context.args:
            await update.message.reply_text("❌ Укажите имя.\nПример: `/monitorcleanup Флудилка`")
            return
        
        name = " ".join(context.args)
        self.monitored_topics[name] = {
            "chat_id": update.message.chat.id, 
            "thread_id": update.message.message_thread_id,
            "cleanup_time": "18:00",
            "messages": []
        }
        self.save_data()
        self.schedule_monitored_cleanup()
        await update.message.reply_text(f"✅ Тема для АВТО-ОЧИСТКИ '{name}' зарегистрирована.")

    def get_monitored_topic_name(self, chat_id, thread_id):
        """Получение имени темы по ID чата и потока."""
        for name, data in self.monitored_topics.items():
            if data['chat_id'] == chat_id and data['thread_id'] == thread_id:
                return name
        return None

    async def handle_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Основной обработчик текстовых сообщений и подписей в группе."""
        
        # --- БЛОК ЗАПРЕЩЕННЫХ СЛОВ ---
        if self.forbidden_words:
            text_lower = (update.message.text or update.message.caption or "").lower()
            if text_lower:
                for word in self.forbidden_words:
                    if word in text_lower:
                        try:
                            await update.message.delete()
                            logger.info(f"Удалено (стоп-слово: '{word}'): {update.message.message_id}")
                        except Exception as e:
                            logger.error(f"Ошибка удаления (стоп-слово): {e}")
                        return 
        # --- КОНЕЦ БЛОКА ---
            
        # --- БЛОК РЕЖИМА ТИШИНЫ (Бесшумный) ---
        if self.is_silent_time():
            try:
                await update.message.delete()
            except Exception as e:
                logger.error(f"Ошибка удаления (режим тишины): {e}")
            return 
        # --- КОНЕЦ БЛОКА ---
            
        # --- БЛОК СБОРА СООБЩЕНИЙ ДЛЯ АВТО-ОЧИСТКИ ---
        topic_name = self.get_monitored_topic_name(update.message.chat_id, update.message.message_thread_id)
        if topic_name and update.message.message_id:
            self.monitored_topics[topic_name]['messages'].append({
                "message_id": update.message.message_id, 
                "user_id": update.message.from_user.id
            })

    # -----------------------------------------------------------------
    # ОБРАБОТЧИКИ В ЛИЧНОМ ЧАТЕ (МЕНЮ - Async)
    # -----------------------------------------------------------------

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда старт (Главное меню в ЛС)"""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени (UTC)", callback_data="timers")],
            [InlineKeyboardButton("📅 Ежедневные приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("🧹 Темы с авто-очисткой", callback_data="monitored_topics")],
            [InlineKeyboardButton("🚫 Запрещенные слова", callback_data="stoplist_menu")], 
            [InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("👋 Главное меню:", reply_markup=reply_markup)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Основной обработчик кнопок (ЛС)"""
        query = update.callback_query
        await query.answer()
        data = query.data
        
        # Общие команды (для предотвращения слишком длинного кода, я опущу здесь детали всех меню, 
        # но они есть в полной версии, которую я вам давал ранее. Я оставлю только функции-заглушки)
        
        if data == "back_main":
            context.user_data.clear()
            await self.show_main_menu(query)
        elif data == "modes": await self.show_modes_menu(query)
        elif data == "timers": await self.show_timers_menu(query)
        elif data == "status": await self.show_status(query)
        elif data.startswith("mode_"): await self.handle_mode_change(query, data)
        elif data.startswith("timer_"): await self.handle_timer_change(query, data, context)
        
        # ... (здесь должна быть вся остальная логика меню)
        
    async def show_main_menu(self, query):
        """Отображение главного меню."""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени (UTC)", callback_data="timers")],
            [InlineKeyboardButton("📅 Ежедневные приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("🧹 Темы с авто-очисткой", callback_data="monitored_topics")],
            [InlineKeyboardButton("🚫 Запрещенные слова", callback_data="stoplist_menu")], 
            [InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await query.edit_message_text("👋 Главное меню:", reply_markup=reply_markup)
        except Exception: pass

    # ... (здесь должны быть все остальные функции show_X_menu и handle_X)
    
    # -----------------------------------------------------------------
    # ОБРАБОТЧИКИ ТЕКСТА В ЛС (ВВОД ДАННЫХ - Async)
    # -----------------------------------------------------------------
    async def handle_private_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода текста (времен, сообщений) в ЛС."""
        user_data = context.user_data
        text = update.message.text
            
        if text.lower() == "/cancel":
            user_data.clear()
            await update.message.reply_text("❌ Действие отменено.")
            await self.start(update, context)
            return
        
        # --- Ввод запрещенного слова ---
        if user_data.get('waiting_stoplist_add'):
            word = text.strip().lower()
            if word in self.forbidden_words:
                await update.message.reply_text("⚠️ Это слово уже есть в списке.")
                return
                
            self.forbidden_words.append(word)
            self.save_data()
            await update.message.reply_text(
                f"✅ Слово '`{word}`' добавлено.\n\n"
                "Введите следующее слово или фразу:",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Готово (Назад)", callback_data="back_stoplist")]])
            )
            user_data.pop('waiting_stoplist_add', None) # Сброс, если нужно только одно слово
            return
            
        # --- Обработка ввода времени (Часть логики для примера) ---
        if user_data.get('waiting_welcome_time'):
            if self.validate_time(text):
                self.welcome_time = text
                self.save_data(); self.schedule_welcome_message()
                await update.message.reply_text(f"✅ Время приветствия (UTC) установлено: {text}")
                # Предполагаем, что у вас есть функция для возврата в меню
                # await self.show_timers_menu_from_message(update)
                user_data.clear()
            else: await update.message.reply_text("❌ Неверный формат! (ЧЧ:ММ)")
            return
            
        # ... (остальная логика ввода данных)

    # -----------------------------------------------------------------
    # УТИЛИТЫ
    # -----------------------------------------------------------------
    def validate_time(self, time_str):
        """Проверка корректности формата времени ЧЧ:ММ."""
        try: datetime.strptime(time_str, "%H:%M"); return True
        except ValueError: return False
        
    def get_current_target_name(self):
        """Получение имени целевой темы для приветствий."""
        if not self.target_chat_id: return None
        for name, data in self.registered_topics.items():
            if data['chat_id'] == self.target_chat_id and data['thread_id'] == self.target_thread_id:
                return name
        return None 

    async def show_status(self, query):
        """Отображение текущего статуса бота."""
        silent_status = "🔇 ВКЛ" if self.silent_mode else "🔊 ВЫКЛ"
        welcome_status = "👋 ВКЛ" if self.welcome_mode else "🚫 ВЫКЛ"
        target_topic_name = self.get_current_target_name()
        
        text = f"ℹ️ **Текущий статус бота**\n\n" \
               f"**Запрещенные слова:**\n" \
               f"• В списке: **{len(self.forbidden_words)}** шт.\n\n" \
               f"**Режим тишины:**\n" \
               f"• Статус: **{silent_status}**\n" \
               f"• Период (UTC): **{self.silent_start_time} - {self.silent_end_time}**"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# -----------------------------------------------------------------------------
# ЗАПУСК БОТА (PTB v20)
# -----------------------------------------------------------------------------
def main():
    if BOT_TOKEN == "8525784017:AAGyonwOxkChbavfqMhT1e4IFLa89mgt_Ys":
        logger.error("!!!!!!!!!!!!!!!!! ОШИБКА !!!!!!!!!!!!!!!!!")
        logger.error("Не указан токен бота (BOT_TOKEN).")
        return

    application = Application.builder().token(BOT_TOKEN).build()
    bot_instance = DailyMessageBot(application)

    # 1. Команды
    application.add_handler(CommandHandler("start", bot_instance.start, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("registertopic", bot_instance.register_topic, filters=filters.ChatType.GROUPS))
    application.add_handler(CommandHandler("monitorcleanup", bot_instance.register_monitor_topic, filters=filters.ChatType.GROUPS))

    # 2. Обработчик кнопок (ЛС)
    application.add_handler(CallbackQueryHandler(bot_instance.button_handler))
    
    # 3. Обработчик текста в ЛС (Ввод данных)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, bot_instance.handle_private_text))
    
    # 4. Обработчик текста и ПОДПИСЕЙ в ГРУППАХ (удаление стоп-слов/тишины, сбор сообщений)
    application.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND & filters.ChatType.GROUPS, 
        bot_instance.handle_group_message
    ))
    
    logger.info("Бот запускается (PTB v20)...")
    application.run_polling(poll_interval=1.0)
    
    bot_instance.scheduler.shutdown()
    logger.info("Бот остановлен.")

if __name__ == '__main__':
    main()
