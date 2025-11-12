import logging
import json
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Updater, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    Filters, 
    CallbackContext
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# -----------------------------------------------------------------------------
# ВАЖНАЯ НАСТРОЙКА
# -----------------------------------------------------------------------------
# ⚠️ ЗАМЕНИТЕ ЭТОТ ТОКЕН НА СВОЙ! 
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE" 
# -----------------------------------------------------------------------------

class DailyMessageBot:
    def __init__(self, updater):
        self.bot = updater.bot
        self.data_file = "bot_data.json"
        
        # Настройки режимов
        self.silent_mode = False
        self.silent_start_time = "18:30"
        self.silent_end_time = "08:00"
        self.welcome_mode = True
        self.welcome_time = "09:00"
        
        # Хранилища
        self.daily_messages = {}
        
        # --- НОВЫЕ ПОЛЯ (из прошлых запросов) ---
        self.registered_topics = {} 
        self.target_chat_id = None  
        self.target_thread_id = None 
        self.welcome_delete_time = "10:00"
        self.last_welcome_message = {} 
        self.monitored_topics = {} 
        
        # --- НОВЫЕ ПОЛЯ (Запрещенные слова) ---
        self.forbidden_words = []
        # ------------------------------------
        
        self.admin_cache = {}
        self.scheduler = BackgroundScheduler(timezone=pytz.UTC)
        self.load_data()
        self.setup_schedulers()
        
    def load_data(self):
        """Загрузка данных из файла"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.silent_mode = data.get('silent_mode', False)
                    self.silent_start_time = data.get('silent_start_time', "18:30")
                    self.silent_end_time = data.get('silent_end_time', "08:00")
                    self.welcome_mode = data.get('welcome_mode', True)
                    self.welcome_time = data.get('welcome_time', "09:00")
                    self.daily_messages = data.get('daily_messages', {})
                    
                    self.registered_topics = data.get('registered_topics', {})
                    self.target_chat_id = data.get('target_chat_id', None)
                    self.target_thread_id = data.get('target_thread_id', None)
                    
                    self.welcome_delete_time = data.get('welcome_delete_time', "10:00")
                    self.last_welcome_message = data.get('last_welcome_message', {})
                    self.monitored_topics = data.get('monitored_topics', {})
                    
                    # (Request 2)
                    self.forbidden_words = data.get('forbidden_words', [])

                    # Восстанавливаем пустые списки сообщений
                    for name in self.monitored_topics:
                        if 'messages' not in self.monitored_topics[name]:
                            self.monitored_topics[name]['messages'] = []

        except Exception as e:
            logging.error(f"Ошибка загрузки данных: {e}")
    
    def save_data(self):
        """Сохранение данных в файл"""
        try:
            monitored_topics_to_save = {}
            for name, data in self.monitored_topics.items():
                monitored_topics_to_save[name] = data.copy()
                monitored_topics_to_save[name].pop('messages', None) # Не сохраняем кеш сообщений

            data = {
                'silent_mode': self.silent_mode,
                'silent_start_time': self.silent_start_time,
                'silent_end_time': self.silent_end_time,
                'welcome_mode': self.welcome_mode,
                'welcome_time': self.welcome_time,
                'daily_messages': self.daily_messages,
                
                'registered_topics': self.registered_topics,
                'target_chat_id': self.target_chat_id,
                'target_thread_id': self.target_thread_id,
                
                'welcome_delete_time': self.welcome_delete_time,
                'last_welcome_message': self.last_welcome_message,
                
                'monitored_topics': monitored_topics_to_save,
                
                'forbidden_words': self.forbidden_words, # (Request 2)
            }
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Ошибка сохранения данных: {e}")

    # -----------------------------------------------------------------
    # ПЛАНИРОВЩИКИ (Без изменений)
    # -----------------------------------------------------------------
    def setup_schedulers(self):
        self.schedule_welcome_message()
        self.schedule_welcome_delete()
        self.schedule_monitored_cleanup()
        if not self.scheduler.running:
            try:
                self.scheduler.start()
            except Exception as e:
                logging.warning(f"Планировщик уже запущен: {e}")

    def schedule_welcome_message(self):
        try: self.scheduler.remove_job('welcome_message')
        except: pass
        try:
            hour, minute = map(int, self.welcome_time.split(':'))
            self.scheduler.add_job(self.send_welcome_message_job, CronTrigger(hour=hour, minute=minute, timezone=pytz.UTC), id='welcome_message')
            logging.info(f"✅ Приветствие: {self.welcome_time} UTC")
        except Exception as e: logging.error(f"Ошибка schedule_welcome_message: {e}")

    def schedule_welcome_delete(self):
        try: self.scheduler.remove_job('welcome_delete')
        except: pass
        try:
            hour, minute = map(int, self.welcome_delete_time.split(':'))
            self.scheduler.add_job(self.delete_welcome_message_job, CronTrigger(hour=hour, minute=minute, timezone=pytz.UTC), id='welcome_delete')
            logging.info(f"✅ Удаление приветствия: {self.welcome_delete_time} UTC")
        except Exception as e: logging.error(f"Ошибка schedule_welcome_delete: {e}")

    def schedule_monitored_cleanup(self):
        for topic_name, topic_data in self.monitored_topics.items():
            job_id = f'cleanup_{topic_name}'
            try: self.scheduler.remove_job(job_id)
            except: pass
            try:
                hour, minute = map(int, topic_data['cleanup_time'].split(':'))
                self.scheduler.add_job(self.cleanup_topic_job, CronTrigger(hour=hour, minute=minute, timezone=pytz.UTC), args=[topic_name], id=job_id)
                logging.info(f"✅ Очистка '{topic_name}': {topic_data['cleanup_time']} UTC")
            except Exception as e: logging.error(f"Ошибка schedule_monitored_cleanup ({topic_name}): {e}")

    # -----------------------------------------------------------------
    # ЗАДАЧИ ПЛАНИРОВЩИКА (JOBS) (Без изменений)
    # -----------------------------------------------------------------
    def send_welcome_message_job(self):
        try:
            today = datetime.now(pytz.UTC).weekday()
            message = self.daily_messages.get(str(today))
            if not self.welcome_mode or not message or not self.target_chat_id:
                return
            sent_message = self.bot.send_message(chat_id=self.target_chat_id, text=message, message_thread_id=self.target_thread_id)
            self.last_welcome_message = {"chat_id": sent_message.chat_id, "message_id": sent_message.message_id}
            self.save_data()
        except Exception as e: logging.error(f"Ошибка send_welcome_message_job: {e}")

    def delete_welcome_message_job(self):
        if not self.last_welcome_message: return
        try:
            self.bot.delete_message(chat_id=self.last_welcome_message['chat_id'], message_id=self.last_welcome_message['message_id'])
        except Exception as e: logging.warning(f"Не удалось удалить приветствие: {e}")
        finally:
            self.last_welcome_message = {}
            self.save_data()

    def get_admin_ids(self, chat_id):
        now = datetime.now()
        if chat_id in self.admin_cache and (now - self.admin_cache[chat_id]['timestamp']).total_seconds() < 600:
            return self.admin_cache[chat_id]['ids']
        try:
            admins = self.bot.get_chat_administrators(chat_id)
            admin_ids = [admin.user.id for admin in admins]
            self.admin_cache[chat_id] = {'ids': admin_ids, 'timestamp': now}
            return admin_ids
        except Exception as e:
            logging.error(f"Не удалось получить список админов: {e}")
            return []

    def cleanup_topic_job(self, topic_name):
        logging.info(f"🧹 Запуск очистки для темы: {topic_name}")
        if topic_name not in self.monitored_topics: return
            
        topic_data = self.monitored_topics[topic_name]
        chat_id = topic_data['chat_id']
        messages_to_delete = topic_data['messages']
        if not messages_to_delete: return

        admin_ids = self.get_admin_ids(chat_id)
        if not admin_ids: return

        deleted_count = 0
        for msg in messages_to_delete:
            if msg['user_id'] not in admin_ids:
                try:
                    self.bot.delete_message(chat_id=chat_id, message_id=msg['message_id'])
                    deleted_count += 1
                except Exception: pass
        
        logging.info(f"✅ Очистка {topic_name} завершена. Удалено {deleted_count} сообщений.")
        self.monitored_topics[topic_name]['messages'] = []

    # -----------------------------------------------------------------
    # ОБРАБОТЧИКИ СООБЩЕНИЙ В ГРУППЕ
    # -----------------------------------------------------------------
    def is_silent_time(self):
        if not self.silent_mode: return False
        now = datetime.now(pytz.UTC).time()
        start_time = datetime.strptime(self.silent_start_time, "%H:%M").time()
        end_time = datetime.strptime(self.silent_end_time, "%H:%M").time()
        return (start_time <= now <= end_time) if start_time < end_time else (now >= start_time or now <= end_time)

    def check_admin(self, update: Update, context: CallbackContext) -> bool:
        if update.message.chat.type == 'private': return True
        try:
            member = context.bot.get_chat_member(chat_id=update.message.chat.id, user_id=update.message.from_user.id)
            is_admin = member.status in [ChatMember.ADMINISTRATOR, ChatMember.CREATOR]
            if not is_admin:
                update.message.reply_text("❌ Только администраторы.")
            return is_admin
        except Exception as e:
            logging.error(f"Ошибка проверки админа: {e}")
            return False

    def register_topic(self, update: Update, context: CallbackContext):
        if not self.check_admin(update, context): return
        if not context.args:
            update.message.reply_text("❌ Укажите имя.\nПример: `/registertopic Новости`")
            return
        name = " ".join(context.args)
        self.registered_topics[name] = {"chat_id": update.message.chat.id, "thread_id": update.message.message_thread_id}
        self.save_data()
        update.message.reply_text(f"✅ Тема для ПРИВETCTBИЙ '{name}' зарегистрирована.")

    def register_monitor_topic(self, update: Update, context: CallbackContext):
        if not self.check_admin(update, context): return
        if not context.args:
            update.message.reply_text("❌ Укажите имя.\nПример: `/monitorcleanup Флудилка`")
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
        update.message.reply_text(f"✅ Тема для АВТО-ОЧИСТКИ '{name}' зарегистрирована.")

    def get_monitored_topic_name(self, chat_id, thread_id):
        for name, data in self.monitored_topics.items():
            if data['chat_id'] == chat_id and data['thread_id'] == thread_id:
                return name
        return None

    def handle_group_message(self, update: Update, context: CallbackContext):
        """Обработка текстовых сообщений и подписей (только в ГРУППЕ)"""
        
        # --- (Request 2) БЛОК ЗАПРЕЩЕННЫХ СЛОВ ---
        if self.forbidden_words:
            text_lower = (update.message.text or update.message.caption or "").lower()
            if text_lower:
                for word in self.forbidden_words:
                    if word in text_lower:
                        try:
                            update.message.delete()
                            logging.info(f"Удалено (стоп-слово: '{word}'): {update.message.message_id}")
                        except Exception as e:
                            logging.error(f"Ошибка удаления (стоп-слово): {e}")
                        return # Выходим, сообщение удалено
        # --- КОНЕЦ БЛОКА ---
            
        # --- БЛОК РЕЖИМА ТИШИНЫ (Бесшумный) ---
        if self.is_silent_time():
            try:
                update.message.delete()
            except Exception as e:
                logging.error(f"Ошибка удаления (режим тишины): {e}")
            return 
        # --- КОНЕЦ БЛОКА ---
            
        # --- БЛОК СБОРА СООБЩЕНИЙ ---
        topic_name = self.get_monitored_topic_name(update.message.chat_id, update.message.message_thread_id)
        if topic_name:
            self.monitored_topics[topic_name]['messages'].append({
                "message_id": update.message.message_id, 
                "user_id": update.message.from_user.id
            })

    # -----------------------------------------------------------------
    # ОБРАБОТЧИКИ В ЛИЧНОМ ЧАТЕ (МЕНЮ)
    # -----------------------------------------------------------------

    def start(self, update: Update, context: CallbackContext):
        """Команда старт (ЛС)"""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени (UTC)", callback_data="timers")],
            [InlineKeyboardButton("📅 Ежедневные приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("🧹 Темы с авто-очисткой", callback_data="monitored_topics")],
            [InlineKeyboardButton("🚫 Запрещенные слова", callback_data="stoplist_menu")], # (Request 2)
            [InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text("👋 Главное меню:", reply_markup=reply_markup)

    def button_handler(self, update: Update, context: CallbackContext):
        """Обработчик кнопок (ЛС)"""
        query = update.callback_query
        query.answer()
        data = query.data
        
        # --- Навигация по Приветствиям ---
        if data == "daily_messages": self.show_daily_messages_menu(query)
        elif data == "daily_select_topic": self.show_topic_selection_menu(query)
        elif data.startswith("select_topic_"): self.handle_topic_selection(query, data)
        elif data == "unregister_topics": self.show_unregister_topic_menu(query)
        elif data.startswith("unregister_"): self.handle_unregister_topic(query, data)
        elif data.startswith("daily_"): self.handle_daily_messages(query, data, context)

        # --- Навигация по Очистке тем ---
        elif data == "monitored_topics": self.show_monitored_topics_menu(query)
        elif data == "monit_list_timers": self.show_monit_list_timers(query)
        elif data.startswith("set_monit_time_"): self.handle_set_monit_time_menu(query, data, context)
        elif data == "monit_clear_now": self.show_monit_clear_now_menu(query)
        elif data.startswith("run_monit_clear_"): self.handle_monit_clear_now(query, data)
        elif data == "monit_remove": self.show_monit_remove_menu(query)
        elif data.startswith("remove_monit_"): self.handle_monit_remove(query, data)
        
        # --- (Request 2) Навигация по Запрещенным словам ---
        elif data == "stoplist_menu": self.show_stoplist_menu(query)
        elif data == "stoplist_add": self.handle_stoplist_add_menu(query, context)
        elif data == "stoplist_view": self.show_stoplist_view(query)
        elif data == "stoplist_remove": self.show_stoplist_remove_menu(query)
        elif data.startswith("stoplist_del_"): self.handle_stoplist_remove(query, data)

        # --- Общая Навигация ---
        elif data == "modes": self.show_modes_menu(query)
        elif data == "timers": self.show_timers_menu(query)
        elif data == "status": self.show_status(query)
        elif data.startswith("mode_"): self.handle_mode_change(query, data)
        elif data.startswith("timer_"): self.handle_timer_change(query, data, context)
        
        # --- (Request 1) Кнопки "Назад" теперь очищают 'waiting' статусы ---
        elif data == "back_main":
            context.user_data.clear()
            self.show_main_menu(query)
        elif data == "back_daily":
            context.user_data.clear()
            self.show_daily_messages_menu(query)
        elif data == "back_modes":
            context.user_data.clear()
            self.show_modes_menu(query)
        elif data == "back_timers":
            context.user_data.clear()
            self.show_timers_menu(query)
        elif data == "back_monitored":
            context.user_data.clear()
            self.show_monitored_topics_menu(query)
        elif data == "back_stoplist": # (Request 2)
            context.user_data.clear()
            self.show_stoplist_menu(query)
            
        elif data == "confirm_clear": self.handle_confirm_clear(query)

    def show_main_menu(self, query):
        """Главное меню (ЛС)"""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени (UTC)", callback_data="timers")],
            [InlineKeyboardButton("📅 Ежедневные приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("🧹 Темы с авто-очисткой", callback_data="monitored_topics")],
            [InlineKeyboardButton("🚫 Запрещенные слова", callback_data="stoplist_menu")], # (Request 2)
            [InlineKeyboardButton("ℹ️ Статус", callback_data="status")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            query.edit_message_text("👋 Главное меню:", reply_markup=reply_markup)
        except Exception: pass

    def show_timers_menu(self, query):
        """Меню времени (ЛС)"""
        keyboard = [
            [InlineKeyboardButton(f"🕐 Приветствие: {self.welcome_time}", callback_data="timer_welcome")],
            [InlineKeyboardButton(f"🗑️ Удаление приветствия: {self.welcome_delete_time}", callback_data="timer_welcome_delete")],
            [InlineKeyboardButton(f"🔇 Начало тишины: {self.silent_start_time}", callback_data="timer_silent_start")],
            [InlineKeyboardButton(f"🔊 Конец тишины: {self.silent_end_time}", callback_data="timer_silent_end")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text("⏰ Настройка времени (по UTC):", reply_markup=reply_markup)

    def show_modes_menu(self, query):
        """Меню режимов (ЛС) - (Request 1: убрано 'silent_show_warning')"""
        silent_status = "🔇 ВКЛ" if self.silent_mode else "🔊 ВЫКЛ"
        welcome_status = "👋 ВКЛ" if self.welcome_mode else "🚫 ВЫКЛ"
        
        keyboard = [
            [InlineKeyboardButton(f"Режим тишины: {silent_status}", callback_data="mode_silent")],
            [InlineKeyboardButton(f"Режим приветствия: {welcome_status}", callback_data="mode_welcome")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(
            "⚙️ Управление режимами:\n\n"
            "🔇 Режим тишины - бот БЕСШУМНО удаляет сообщения в нерабочее время.\n"
            "👋 Режим приветствия - ежедневное приветственное сообщение.",
            reply_markup=reply_markup
        )

    def handle_mode_change(self, query, data):
        if data == "mode_silent":
            self.silent_mode = not self.silent_mode
            self.save_data()
            self.show_modes_menu(query)
        elif data == "mode_welcome":
            self.welcome_mode = not self.welcome_mode
            self.save_data()
            self.show_modes_menu(query)

    def handle_timer_change(self, query, data, context):
        """(Request 1) Обработка изменения времени (ЛС) - Добавлены кнопки Отмены"""
        cancel_button = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="back_timers")]])
        
        if data == "timer_welcome":
            query.edit_message_text(f"⏰ Введите время для ПРИВЕТСТВИЯ (UTC, ЧЧ:ММ):\nСейчас: {self.welcome_time}", reply_markup=cancel_button)
            context.user_data['waiting_welcome_time'] = True
        
        elif data == "timer_welcome_delete": 
            query.edit_message_text(f"🗑️ Введите время для УДАЛЕНИЯ ПРИВЕТСТВИЯ (UTC, ЧЧ:ММ):\nСейчас: {self.welcome_delete_time}", reply_markup=cancel_button)
            context.user_data['waiting_welcome_delete_time'] = True

        elif data == "timer_silent_start": 
            query.edit_message_text(f"🔇 Введите время НАЧАЛА тишины (UTC, ЧЧ:ММ):\nСейчас: {self.silent_start_time}", reply_markup=cancel_button)
            context.user_data['waiting_silent_start'] = True
        
        elif data == "timer_silent_end": 
            query.edit_message_text(f"🔊 Введите время ОКОНЧАНИЯ тишины (UTC, ЧЧ:ММ):\nСейчас: {self.silent_end_time}", reply_markup=cancel_button)
            context.user_data['waiting_silent_end'] = True
            
    # --- Меню Приветствий (Без изменений) ---
    def show_daily_messages_menu(self, query):
        keyboard = [
            [InlineKeyboardButton("🎯 Выбрать тему для приветствий", callback_data="daily_select_topic")],
            [InlineKeyboardButton("📝 Добавить/изменить приветствие", callback_data="daily_add")],
            [InlineKeyboardButton("👁️ Просмотреть приветствия", callback_data="daily_view")],
            [InlineKeyboardButton("🗑️ Удалить все приветствия", callback_data="daily_clear")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        query.edit_message_text("📅 Управление ежедневными приветствиями:", reply_markup=InlineKeyboardMarkup(keyboard))
    def show_topic_selection_menu(self, query):
        if not self.registered_topics:
            query.edit_message_text("❌ Нет зарегистрированных тем.\n(Используйте `/registertopic Имя` в группе)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_daily")]]))
            return
        keyboard = []
        current_target_name = self.get_current_target_name()
        for name in self.registered_topics.keys():
            icon = "✅" if name == current_target_name else "☑️"
            keyboard.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"select_topic_{name}")])
        keyboard.append([InlineKeyboardButton("🗑️ Удалить регистрацию темы", callback_data="unregister_topics")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_daily")])
        query.edit_message_text("🎯 Выберите тему для приветствий:", reply_markup=InlineKeyboardMarkup(keyboard))
    def handle_topic_selection(self, query, data):
        name = data.replace("select_topic_", "")
        if name not in self.registered_topics:
            query.edit_message_text("❌ Тема не найдена.")
            self.show_topic_selection_menu(query)
            return
        topic_data = self.registered_topics[name]
        self.target_chat_id = topic_data["chat_id"]
        self.target_thread_id = topic_data["thread_id"]
        self.save_data()
        self.show_topic_selection_menu(query) 
    def get_current_target_name(self):
        if not self.target_chat_id: return None
        for name, data in self.registered_topics.items():
            if data['chat_id'] == self.target_chat_id and data['thread_id'] == self.target_thread_id:
                return name
        return None 
    def show_unregister_topic_menu(self, query):
        if not self.registered_topics:
            self.show_topic_selection_menu(query); return
        keyboard = [[InlineKeyboardButton(f"🗑️ {name}", callback_data=f"unregister_{name}")] for name in self.registered_topics.keys()]
        keyboard.append([InlineKeyboardButton("🔙 Назад к выбору тем", callback_data="daily_select_topic")])
        query.edit_message_text("Выберите тему для удаления из списка:", reply_markup=InlineKeyboardMarkup(keyboard))
    def handle_unregister_topic(self, query, data):
        name = data.replace("unregister_", "")
        popped_topic = self.registered_topics.pop(name, None)
        if popped_topic:
            if popped_topic['chat_id'] == self.target_chat_id and popped_topic['thread_id'] == self.target_thread_id:
                self.target_chat_id = None
                self.target_thread_id = None
            self.save_data()
        self.show_topic_selection_menu(query)
    def handle_daily_messages(self, query, data, context):
        if data == "daily_add":
            query.edit_message_text(
                "📝 Добавление приветствия:\n"
                "Формат: <b>День: Сообщение</b>\n"
                "Пример: <code>Пн: Доброе утро!</code>",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="back_daily")]]) # (Request 1)
            )
            context.user_data['waiting_daily_message'] = True
        elif data == "daily_view": self.show_all_messages(query)
        elif data == "daily_clear":
            keyboard = [
                [InlineKeyboardButton("✅ Да, удалить все", callback_data="confirm_clear")],
                [InlineKeyboardButton("❌ Нет, отмена", callback_data="back_daily")]
            ]
            query.edit_message_text("⚠️ Вы уверены, что хотите удалить ВСЕ ежедневные приветствия?", reply_markup=InlineKeyboardMarkup(keyboard))
    def handle_confirm_clear(self, query):
        self.daily_messages.clear(); self.save_data()
        query.edit_message_text("✅ Все ежедневные приветствия удалены.")
        self.show_daily_messages_menu(query)
    def show_all_messages(self, query):
        days_map = {"0": "Пн", "1": "Вт", "2": "Ср", "3": "Чт", "4": "Пт", "5": "Сб", "6": "Вс"}
        text = "📅 Ежедневные приветствия:\n\n"
        if not self.daily_messages:
            text = "❌ Нет установленных приветствий."
        else:
            for day_num, day_name in days_map.items():
                message = self.daily_messages.get(day_num, "❌")
                text += f"<b>{day_name}:</b> {message}\n"
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_daily")]]
        query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
        

    # --- Меню Очистки Тем (Без изменений, кроме кнопки "Отмена") ---
    def show_monitored_topics_menu(self, query):
        keyboard = [
            [InlineKeyboardButton("⏰ Список тем и время очистки", callback_data="monit_list_timers")],
            [InlineKeyboardButton("🗑️ Очистить тему немедленно", callback_data="monit_clear_now")],
            [InlineKeyboardButton("❌ Удалить тему из мониторинга", callback_data="monit_remove")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text("🧹 Управление темами с авто-очисткой:", reply_markup=reply_markup)
    def show_monit_list_timers(self, query):
        if not self.monitored_topics:
            query.edit_message_text("❌ Нет тем на авто-очистке.\n(Используйте `/monitorcleanup Имя` в группе)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_monitored")]]))
            return
        keyboard = []
        for name, data in self.monitored_topics.items():
            time = data.get('cleanup_time', '18:00')
            keyboard.append([InlineKeyboardButton(f"⏰ {name} ({time} UTC)", callback_data=f"set_monit_time_{name}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_monitored")])
        query.edit_message_text("Выберите тему для настройки времени очистки:", reply_markup=InlineKeyboardMarkup(keyboard))
    def handle_set_monit_time_menu(self, query, data, context):
        topic_name = data.replace("set_monit_time_", "")
        if topic_name in self.monitored_topics:
            current_time = self.monitored_topics[topic_name].get('cleanup_time', '18:00')
            query.edit_message_text(
                f"🧹 Тема: <b>{topic_name}</b>\n"
                f"Текущее время (UTC): {current_time}\n"
                f"Введите новое время (ЧЧ:ММ):",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="back_monitored")]]) # (Request 1)
            )
            context.user_data['waiting_monit_cleanup_time'] = topic_name
        else: query.edit_message_text("❌ Тема не найдена.")
    def show_monit_clear_now_menu(self, query):
        if not self.monitored_topics:
            query.edit_message_text("❌ Нет тем.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_monitored")]]))
            return
        keyboard = []
        for name, data in self.monitored_topics.items():
            msg_count = len(data.get('messages', []))
            keyboard.append([InlineKeyboardButton(f"🗑️ {name} ({msg_count} сообщ.)", callback_data=f"run_monit_clear_{name}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_monitored")])
        query.edit_message_text("Выберите тему для очистки (сообщения НЕ-админов):", reply_markup=InlineKeyboardMarkup(keyboard))
    def handle_monit_clear_now(self, query, data):
        topic_name = data.replace("run_monit_clear_", "")
        query.edit_message_text(f"⏳ Запускаю очистку для '{topic_name}'...")
        self.cleanup_topic_job(topic_name)
        query.edit_message_text(f"✅ Очистка '{topic_name}' завершена.")
        self.show_monitored_topics_menu(query)
    def show_monit_remove_menu(self, query):
        if not self.monitored_topics:
            query.edit_message_text("❌ Нет тем.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_monitored")]]))
            return
        keyboard = [[InlineKeyboardButton(f"❌ {name}", callback_data=f"remove_monit_{name}")] for name in self.monitored_topics.keys()]
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_monitored")])
        query.edit_message_text("Удалить тему из мониторинга:", reply_markup=InlineKeyboardMarkup(keyboard))
    def handle_monit_remove(self, query, data):
        topic_name = data.replace("remove_monit_", "")
        if self.monitored_topics.pop(topic_name, None):
            self.save_data()
            try: self.scheduler.remove_job(f'cleanup_{topic_name}')
            except Exception: pass
            query.edit_message_text(f"✅ Тема '{topic_name}' удалена из мониторинга.")
        else: query.edit_message_text(f"❌ Тема '{topic_name}' не найдена.")
        self.show_monitored_topics_menu(query)

    # -----------------------------------------------------------------
    # (Request 2) НОВЫЕ МЕТОДЫ: ЗАПРЕЩЕННЫЕ СЛОВА
    # -----------------------------------------------------------------
    
    def show_stoplist_menu(self, query):
        """Показывает меню управления запрещенными словами"""
        keyboard = [
            [InlineKeyboardButton("➕ Добавить слово/фразу", callback_data="stoplist_add")],
            [InlineKeyboardButton("🗑️ Удалить слово/фразу", callback_data="stoplist_remove")],
            [InlineKeyboardButton("👁️ Показать список", callback_data="stoplist_view")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(
            "🚫 Управление запрещенными словами:\n\n"
            "Бот будет удалять сообщения (включая подписи к медиа) с этими словами.",
            reply_markup=reply_markup
        )
        
    def handle_stoplist_add_menu(self, query, context):
        """Запрос на ввод нового слова"""
        cancel_button = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data="back_stoplist")]])
        query.edit_message_text(
            "Введите слово или фразу для добавления в 'стоп-лист'.\n"
            "(Регистр не важен)",
            reply_markup=cancel_button
        )
        context.user_data['waiting_stoplist_add'] = True
        
    def show_stoplist_view(self, query):
        """Показывает список слов в виде текста"""
        if not self.forbidden_words:
            text = "❌ 'Стоп-лист' пуст."
        else:
            text = "🚫 Запрещенные слова и фразы:\n\n"
            for word in self.forbidden_words:
                text += f"• `{word}`\n"
                
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_stoplist")]]
        query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
        
    def show_stoplist_remove_menu(self, query):
        """Показывает список слов в виде кнопок для удаления"""
        if not self.forbidden_words:
            query.edit_message_text("❌ 'Стоп-лист' пуст. Нечего удалять.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_stoplist")]]))
            return

        keyboard = []
        # Используем индекс, т.к. слово может быть > 64 байт
        for i, word in enumerate(self.forbidden_words):
            # Обрезаем слово для кнопки, если оно слишком длинное
            display_word = word if len(word) < 40 else word[:37] + "..."
            keyboard.append([InlineKeyboardButton(f"🗑️ {display_word}", callback_data=f"stoplist_del_{i}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_stoplist")])
        query.edit_message_text("Нажмите на слово, чтобы удалить его:", reply_markup=InlineKeyboardMarkup(keyboard))

    def handle_stoplist_remove(self, query, data):
        """Удаляет слово по его индексу"""
        try:
            index = int(data.replace("stoplist_del_", ""))
            word = self.forbidden_words.pop(index)
            self.save_data()
            query.answer(f"✅ Слово '{word}' удалено.")
        except (IndexError, ValueError):
            query.answer("⚠️ Ошибка: Слово уже удалено.", show_alert=True)
            
        # Обновляем меню удаления
        self.show_stoplist_remove_menu(query)

    # -----------------------------------------------------------------
    # ОБРАБОТЧИКИ ТЕКСТА В ЛС (ВВОД ДАННЫХ)
    # -----------------------------------------------------------------
    def handle_private_text(self, update: Update, context: CallbackContext):
        user_data = context.user_data
        text = update.message.text
            
        if text.lower() == "/cancel":
            user_data.clear()
            update.message.reply_text("❌ Действие отменено.")
            self.start(update, context)
            return
        
        # --- (Request 2) Ввод запрещенного слова ---
        if user_data.get('waiting_stoplist_add'):
            word = text.strip().lower()
            if not word:
                update.message.reply_text("❌ Слово не может быть пустым.")
                return
            if word in self.forbidden_words:
                update.message.reply_text("⚠️ Это слово уже есть в списке.")
                return
                
            self.forbidden_words.append(word)
            self.save_data()
            query = update.message # Для ответа
            query.reply_text(
                f"✅ Слово '`{word}`' добавлено.\n\n"
                "Введите следующее слово или фразу:",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Готово (Назад)", callback_data="back_stoplist")]])
            )
            # user_data остается активным, чтобы можно было вводить слова подряд
            return
            
        # --- Обработка ввода времени ---
        if user_data.get('waiting_welcome_time'):
            if self.validate_time(text):
                self.welcome_time = text
                self.save_data(); self.schedule_welcome_message()
                update.message.reply_text(f"✅ Время приветствия (UTC) установлено: {text}")
                self.show_timers_menu_from_message(update)
                user_data.clear()
            else: update.message.reply_text("❌ Неверный формат! (ЧЧ:ММ)")
            return
            
        elif user_data.get('waiting_welcome_delete_time'):
            if self.validate_time(text):
                self.welcome_delete_time = text
                self.save_data(); self.schedule_welcome_delete()
                update.message.reply_text(f"✅ Время удаления (UTC) установлено: {text}")
                self.show_timers_menu_from_message(update)
                user_data.clear()
            else: update.message.reply_text("❌ Неверный формат! (ЧЧ:ММ)")
            return

        elif user_data.get('waiting_silent_start'):
            if self.validate_time(text):
                self.silent_start_time = text
                self.save_data()
                update.message.reply_text(f"✅ Начало тишины (UTC) установлено: {text}")
                self.show_timers_menu_from_message(update)
                user_data.clear()
            else: update.message.reply_text("❌ Неверный формат! (ЧЧ:ММ)")
            return
        
        elif user_data.get('waiting_silent_end'):
            if self.validate_time(text):
                self.silent_end_time = text
                self.save_data()
                update.message.reply_text(f"✅ Конец тишины (UTC) установлено: {text}")
                self.show_timers_menu_from_message(update)
                user_data.clear()
            else: update.message.reply_text("❌ Неверный формат! (ЧЧ:ММ)")
            return
            
        elif user_data.get('waiting_monit_cleanup_time'):
            topic_name = user_data['waiting_monit_cleanup_time']
            if self.validate_time(text):
                if topic_name in self.monitored_topics:
                    self.monitored_topics[topic_name]['cleanup_time'] = text
                    self.save_data(); self.schedule_monitored_cleanup()
                    update.message.reply_text(f"✅ Время очистки (UTC) для '{topic_name}': {text}")
                    self.show_monitored_topics_menu_from_message(update)
                else: update.message.reply_text("❌ Тема не найдена!")
                user_data.clear()
            else: update.message.reply_text("❌ Неверный формат! (ЧЧ:ММ)")
            return
            
        elif user_data.get('waiting_daily_message'):
            try:
                day_part, message = text.split(":", 1)
                day_part = day_part.strip().lower()
                message = message.strip()
                days_map = {"понедельник": "0", "пн": "0", "вторник": "1", "вт": "1", "среда": "2", "ср": "2", "четверг": "3", "чт": "3", "пятница": "4", "пт": "4", "суббота": "5", "сб": "5", "воскресенье": "6", "вс": "6"}
                
                if day_part in days_map and message:
                    day_num = days_map[day_part]
                    self.daily_messages[day_num] = message
                    self.save_data()
                    update.message.reply_text(f"✅ Приветствие для {self.get_day_name(day_num)} установлено.\n\nВведите следующее или нажмите 'Отмена'.",
                                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Готово (Назад)", callback_data="back_daily")]]))
                else:
                    update.message.reply_text("❌ Неверный день или пустое сообщение!")
            except Exception:
                update.message.reply_text("❌ Неверный формат! (День: Сообщение)")
            return

    # -----------------------------------------------------------------
    # УТИЛИТЫ И СТАТУС (Обновлен статус)
    # -----------------------------------------------------------------
    def get_day_name(self, day_num):
        days = { "0": "Пн", "1": "Вт", "2": "Ср", "3": "Чт", "4": "Пт", "5": "Сб", "6": "Вс" }
        return days.get(day_num, "?")

    def validate_time(self, time_str):
        try: datetime.strptime(time_str, "%H:%M"); return True
        except ValueError: return False

    def show_timers_menu_from_message(self, update):
        keyboard = [
            [InlineKeyboardButton(f"🕐 Приветствие: {self.welcome_time}", callback_data="timer_welcome")],
            [InlineKeyboardButton(f"🗑️ Удаление: {self.welcome_delete_time}", callback_data="timer_welcome_delete")],
            [InlineKeyboardButton(f"🔇 Начало тишины: {self.silent_start_time}", callback_data="timer_silent_start")],
            [InlineKeyboardButton(f"🔊 Конец тишины: {self.silent_end_time}", callback_data="timer_silent_end")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        update.message.reply_text("⏰ Настройка времени (UTC):", reply_markup=InlineKeyboardMarkup(keyboard))

    def show_monitored_topics_menu_from_message(self, update):
        keyboard = [
            [InlineKeyboardButton("⏰ Список тем и время очистки", callback_data="monit_list_timers")],
            [InlineKeyboardButton("🗑️ Очистить тему немедленно", callback_data="monit_clear_now")],
            [InlineKeyboardButton("❌ Удалить тему из мониторинга", callback_data="monit_remove")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        update.message.reply_text("🧹 Управление темами с авто-очисткой:", reply_markup=InlineKeyboardMarkup(keyboard))

    def show_status(self, query):
        """Показать статус бота (ЛС)"""
        silent_status = "🔇 ВКЛ" if self.silent_mode else "🔊 ВЫКЛ"
        welcome_status = "👋 ВКЛ" if self.welcome_mode else "🚫 ВЫКЛ"
        filled_days = sum(1 for i in range(7) if str(i) in self.daily_messages)
        target_topic_name = self.get_current_target_name()
        
        if target_topic_name:
            target_info = f"✅ **{target_topic_name}**"
        else:
            target_info = "❌ **НЕ ВЫБРАНА**"
        
        text = f"ℹ️ **Текущий статус бота**\n\n" \
               f"**Запрещенные слова (Req 2):**\n" \
               f"• В списке: **{len(self.forbidden_words)}** шт.\n\n" \
               f"**Режим тишины (Req 1):**\n" \
               f"• Статус: **{silent_status}**\n" \
               f"• Период (UTC): **{self.silent_start_time} - {self.silent_end_time}**\n\n" \
               f"**Авто-очистка тем:**\n" \
               f"• Тем на мониторинге: **{len(self.monitored_topics)}**\n\n" \
               f"**Приветствия:**\n" \
               f"• Статус: **{welcome_status}**\n" \
               f"• Отправка (UTC): **{self.welcome_time}**\n" \
               f"• Удаление (UTC): **{self.welcome_delete_time}**\n" \
               f"• Настроено дней: **{filled_days} / 7**\n" \
               f"• Целевая тема: {target_info}"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]]
        query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# -----------------------------------------------------------------------------
# ЗАПУСК БОТА (с фильтром 'caption')
# -----------------------------------------------------------------------------
def main():
    if BOT_TOKEN == "8525784017:AAGyonwOxkChbavfqMhT1e4IFLa89mgt_Ys":
        logging.error("!!!!!!!!!!!!!!!!! ОШИБКА !!!!!!!!!!!!!!!!!")
        logging.error("Не указан токен бота (BOT_TOKEN).")
        return

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    bot_instance = DailyMessageBot(updater)

    # 1. Команды в ЛС
    dp.add_handler(CommandHandler("start", bot_instance.start, filters=Filters.chat_type.private))

    # 2. Команды в ГРУППАХ
    dp.add_handler(CommandHandler("registertopic", bot_instance.register_topic, filters=Filters.chat_type.groups))
    dp.add_handler(CommandHandler("monitorcleanup", bot_instance.register_monitor_topic, filters=Filters.chat_type.groups))

    # 3. Обработчик кнопок (ЛС)
    dp.add_handler(CallbackQueryHandler(bot_instance.button_handler))
    
    # 4. Обработчик текста в ЛС (Ввод данных)
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command & Filters.chat_type.private, bot_instance.handle_private_text))
    
    # 5. Обработчик текста и ПОДПИСЕЙ в ГРУППАХ
    dp.add_handler(MessageHandler(
        (Filters.text | Filters.caption) & ~Filters.command & Filters.chat_type.groups, 
        bot_instance.handle_group_message
    ))
    
    logging.info("Бот запускается...")
    updater.start_polling()
    updater.idle()
    
    bot_instance.scheduler.shutdown()
    logging.info("Бот остановлен.")

if __name__ == '__main__':
    main()
