import logging
import json
import os
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
        self.data_file = "bot_data.json"
        self.silent_mode = False
        self.silent_show_warning = False
        self.silent_start_time = "22:00"
        self.silent_end_time = "08:00"
        self.welcome_time = "09:00"
        self.cleanup_time = "18:00"
        self.welcome_mode = True
        self.daily_messages = {}
        self.work_topics = {}
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
                    self.silent_show_warning = data.get('silent_show_warning', False)
                    self.silent_start_time = data.get('silent_start_time', "22:00")
                    self.silent_end_time = data.get('silent_end_time', "08:00")
                    self.welcome_mode = data.get('welcome_mode', True)
                    self.welcome_time = data.get('welcome_time', "09:00")
                    self.cleanup_time = data.get('cleanup_time', "18:00")
                    self.daily_messages = data.get('daily_messages', {})
                    self.work_topics = data.get('work_topics', {})
        except Exception as e:
            logging.error(f"Ошибка загрузки данных: {e}")
    
    def save_data(self):
        """Сохранение данных в файл"""
        try:
            data = {
                'silent_mode': self.silent_mode,
                'silent_show_warning': self.silent_show_warning,
                'silent_start_time': self.silent_start_time,
                'silent_end_time': self.silent_end_time,
                'welcome_mode': self.welcome_mode,
                'welcome_time': self.welcome_time,
                'cleanup_time': self.cleanup_time,
                'daily_messages': self.daily_messages,
                'work_topics': self.work_topics
            }
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Ошибка сохранения данных: {e}")

    def setup_schedulers(self):
        """Настройка планировщиков"""
        if self.welcome_mode and self.daily_messages:
            self.schedule_welcome_message()
        
        self.schedule_topic_cleanup()
        self.scheduler.start()

    def schedule_welcome_message(self):
        """Планировщик для приветственных сообщений"""
        try:
            self.scheduler.remove_job('welcome_message')
        except:
            pass
            
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

    def schedule_topic_cleanup(self):
        """Планировщик для очистки тем"""
        for topic_name, topic_data in self.work_topics.items():
            if 'cleanup_time' in topic_data:
                try:
                    job_id = f'cleanup_{topic_name}'
                    self.scheduler.remove_job(job_id)
                except:
                    pass
                    
                try:
                    cleanup_time = topic_data['cleanup_time']
                    cleanup_hour, cleanup_minute = map(int, cleanup_time.split(':'))
                    
                    self.scheduler.add_job(
                        self.clean_topic_messages_job,
                        CronTrigger(hour=cleanup_hour, minute=cleanup_minute),
                        args=[topic_name],
                        id=job_id
                    )
                    logging.info(f"✅ Очистка темы '{topic_name}' запланирована на {cleanup_time}")
                except Exception as e:
                    logging.error(f"Ошибка планировщика для темы {topic_name}: {e}")

    def send_welcome_message_job(self):
        """Задача для отправки приветственного сообщения"""
        logging.info("✅ Запуск отправки приветственного сообщения")

    def clean_topic_messages_job(self, topic_name):
        """Задача для очистки сообщений в теме"""
        if topic_name in self.work_topics:
            message_count = len(self.work_topics[topic_name].get('messages', []))
            self.work_topics[topic_name]['messages'] = []
            self.save_data()
            logging.info(f"✅ Очищено {message_count} сообщений в теме '{topic_name}'")

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
            [InlineKeyboardButton("📅 Ежедневные приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("💬 Рабочие темы", callback_data="work_topics")],
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
        elif data == "daily_messages":
            self.show_daily_messages_menu(query)
        elif data == "work_topics":
            self.show_work_topics_menu(query)
        elif data == "status":
            self.show_status(query)
        elif data.startswith("mode_"):
            self.handle_mode_change(query, data)
        elif data.startswith("timer_"):
            self.handle_timer_change(query, data)
        elif data.startswith("daily_"):
            self.handle_daily_messages(query, data)
        elif data.startswith("topic_"):
            self.handle_work_topics(query, data)
        elif data == "back_main":
            self.show_main_menu(query)
        elif data == "back_daily":
            self.show_daily_messages_menu(query)
        elif data == "back_modes":
            self.show_modes_menu(query)
        elif data == "back_timers":
            self.show_timers_menu(query)
        elif data == "back_topics":
            self.show_work_topics_menu(query)

    def show_main_menu(self, query):
        """Главное меню"""
        keyboard = [
            [InlineKeyboardButton("⚙️ Управление режимами", callback_data="modes")],
            [InlineKeyboardButton("⏰ Настройка времени", callback_data="timers")],
            [InlineKeyboardButton("📅 Ежедневные приветствия", callback_data="daily_messages")],
            [InlineKeyboardButton("💬 Рабочие темы", callback_data="work_topics")],
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
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            "⏰ Настройка времени:\n\n"
            "🕐 Приветствие - когда отправлять приветствие\n"
            "🔇 Начало тишины - когда включать режим тишины\n"  
            "🔊 Конец тишины - когда выключать режим тишины\n\n"
            "💡 Формат: ЧЧ:ММ (например: 22:30 или 08:15)",
            reply_markup=reply_markup
        )

    def show_modes_menu(self, query):
        """Меню режимов"""
        silent_status = "🔇 ВКЛ" if self.silent_mode else "🔊 ВЫКЛ"
        warning_status = "✅ ВКЛ" if self.silent_show_warning else "❌ ВЫКЛ"
        welcome_status = "👋 ВКЛ" if self.welcome_mode else "🚫 ВЫКЛ"
        
        keyboard = [
            [InlineKeyboardButton(f"Режим тишины: {silent_status}", callback_data="mode_silent")],
            [InlineKeyboardButton(f"Показ предупреждения: {warning_status}", callback_data="mode_warning")],
            [InlineKeyboardButton(f"Режим приветствия: {welcome_status}", callback_data="mode_welcome")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            "⚙️ Управление режимами:\n\n"
            "🔇 Режим тишины - бот удаляет сообщения в нерабочее время\n"
            "⚠️ Показ предупреждения - показывать сообщение при удалении\n"
            "👋 Режим приветствия - ежедневное приветственное сообщение",
            reply_markup=reply_markup
        )

    def show_daily_messages_menu(self, query):
        """Меню ежедневных приветствий"""
        keyboard = [
            [InlineKeyboardButton("📝 Добавить/изменить приветствие", callback_data="daily_add")],
            [InlineKeyboardButton("👁️ Просмотреть приветствия", callback_data="daily_view")],
            [InlineKeyboardButton("🗑️ Удалить все приветствия", callback_data="daily_clear")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            "📅 Управление ежедневными приветствиями:\n\n"
            "Установите разные приветствия на каждый день недели.",
            reply_markup=reply_markup
        )

    def show_work_topics_menu(self, query):
        """Меню рабочих тем"""
        keyboard = [
            [InlineKeyboardButton("➕ Создать тему", callback_data="topic_create")],
            [InlineKeyboardButton("📋 Список тем", callback_data="topic_list")],
            [InlineKeyboardButton("⏰ Настройка времени очистки", callback_data="topic_timer")],
            [InlineKeyboardButton("🗑️ Очистить сообщения в теме", callback_data="topic_clear")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            "💬 Управление рабочими темами:\n\n"
            "Создавайте темы для обсуждения рабочих вопросов. "
            "Все сообщения в теме будут автоматически удаляться в конце рабочего дня.",
            reply_markup=reply_markup
        )

    def handle_timer_change(self, query, data):
        """Обработка изменения времени"""
        if data == "timer_welcome":
            query.edit_message_text(
                "⏰ Введите время для приветствия (формат ЧЧ:ММ):\n"
                f"Сейчас: {self.welcome_time}\n\n"
                "Примеры: 09:00, 10:30, 08:15\n\n"
                "❌ Отмена - /cancel"
            )
            context.user_data['waiting_welcome_time'] = True
        
        elif data == "timer_silent_start":
            query.edit_message_text(
                "🔇 Введите время начала тишины (формат ЧЧ:ММ):\n"
                f"Сейчас: {self.silent_start_time}\n\n"
                "Примеры: 22:00, 23:30, 00:15\n\n"
                "❌ Отмена - /cancel"
            )
            context.user_data['waiting_silent_start'] = True
        
        elif data == "timer_silent_end":
            query.edit_message_text(
                "🔊 Введите время окончания тишины (формат ЧЧ:ММ):\n"
                f"Сейчас: {self.silent_end_time}\n\n"
                "Примеры: 08:00, 07:30, 09:15\n\n"
                "❌ Отмена - /cancel"
            )
            context.user_data['waiting_silent_end'] = True

    def handle_mode_change(self, query, data):
        """Обработка изменения режимов"""
        if data == "mode_silent":
            self.silent_mode = not self.silent_mode
            status = "включен" if self.silent_mode else "выключен"
            query.edit_message_text(f"✅ Режим тишины {status}!")
            self.save_data()
            self.show_modes_menu(query)
            
        elif data == "mode_warning":
            self.silent_show_warning = not self.silent_show_warning
            status = "включен" if self.silent_show_warning else "выключен"
            query.edit_message_text(f"✅ Показ предупреждения {status}!")
            self.save_data()
            self.show_modes_menu(query)
            
        elif data == "mode_welcome":
            self.welcome_mode = not self.welcome_mode
            status = "включен" if self.welcome_mode else "выключен"
            query.edit_message_text(f"✅ Режим приветствия {status}!")
            if self.welcome_mode:
                self.schedule_welcome_message()
            self.save_data()
            self.show_modes_menu(query)

    def handle_daily_messages(self, query, data):
        """Обработка ежедневных сообщений"""
        if data == "daily_add":
            query.edit_message_text(
                "📝 Добавление приветствия:\n\n"
                "Отправьте сообщение в формате:\n"
                "<b>День недели: Сообщение</b>\n\n"
                "Примеры:\n"
                "<code>Понедельник: Доброе утро! Хорошей недели!</code>\n"
                "<code>Вторник: Привет! Хорошего дня!</code>\n\n"
                "Можно использовать сокращения:\n"
                "Пн, Вт, Ср, Чт, Пт, Сб, Вс\n\n"
                "❌ Для отмены отправьте /cancel",
                parse_mode='HTML'
            )
            context.user_data['waiting_daily_message'] = True
        
        elif data == "daily_view":
            self.show_all_messages(query)
        
        elif data == "daily_clear":
            keyboard = [
                [InlineKeyboardButton("✅ Да, удалить все", callback_data="confirm_clear")],
                [InlineKeyboardButton("❌ Нет, отмена", callback_data="back_daily")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            query.edit_message_text(
                "⚠️ Вы уверены, что хотите удалить ВСЕ ежедневные приветствия?",
                reply_markup=reply_markup
            )
        
        elif data == "confirm_clear":
            self.daily_messages.clear()
            self.save_data()
            query.edit_message_text("✅ Все ежедневные приветствия удалены!")
            self.show_daily_messages_menu(query)

    def show_all_messages(self, query):
        """Показать все ежедневные сообщения"""
        days_map = {
            "0": "Понедельник", 
            "1": "Вторник", 
            "2": "Среда", 
            "3": "Четверг", 
            "4": "Пятница", 
            "5": "Суббота", 
            "6": "Воскресенье"
        }
        
        if not self.daily_messages:
            text = "❌ Нет установленных приветствий!\n\nНажмите «Добавить/изменить приветствие» чтобы создать первое приветствие."
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_daily")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            query.edit_message_text(text, reply_markup=reply_markup)
            return
        
        text = "📅 Ежедневные приветствия:\n\n"
        for day_num, day_name in days_map.items():
            message = self.daily_messages.get(day_num, "❌ Не установлено")
            status = "✅" if day_num in self.daily_messages else "❌"
            text += f"{status} <b>{day_name}:</b>\n{message}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_daily")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)

    def handle_work_topics(self, query, data):
        """Обработка рабочих тем"""
        if data == "topic_create":
            query.edit_message_text("💬 Введите название для новой темы:\n\n❌ Отмена - /cancel")
            context.user_data['waiting_topic_name'] = True
        
        elif data == "topic_list":
            if not self.work_topics:
                query.edit_message_text("📭 Нет активных тем!")
                return
            
            topics_text = "📋 Активные темы:\n\n"
            for topic_name, topic_data in self.work_topics.items():
                created = topic_data.get('created', 'Неизвестно')
                cleanup_time = topic_data.get('cleanup_time', '18:00')
                message_count = len(topic_data.get('messages', []))
                topics_text += f"• <b>{topic_name}</b>\n  🕐 Очистка: {cleanup_time}\n  💬 Сообщений: {message_count}\n\n"
            
            query.edit_message_text(topics_text, parse_mode='HTML')
        
        elif data == "topic_timer":
            if not self.work_topics:
                query.edit_message_text("❌ Нет тем для настройки!")
                return
            
            keyboard = []
            for topic_name in self.work_topics.keys():
                cleanup_time = self.work_topics[topic_name].get('cleanup_time', '18:00')
                keyboard.append([InlineKeyboardButton(f"⏰ {topic_name} ({cleanup_time})", callback_data=f"set_time_{topic_name}")])
            
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_topics")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            query.edit_message_text("Выберите тему для настройки времени очистки:", reply_markup=reply_markup)
        
        elif data == "topic_clear":
            if not self.work_topics:
                query.edit_message_text("❌ Нет тем для очистки!")
                return
            
            keyboard = []
            for topic_name in self.work_topics.keys():
                message_count = len(self.work_topics[topic_name].get('messages', []))
                keyboard.append([InlineKeyboardButton(f"🗑️ {topic_name} ({message_count} сообщ.)", callback_data=f"clear_topic_{topic_name}")])
            
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_topics")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            query.edit_message_text("Выберите тему для очистки сообщений:", reply_markup=reply_markup)
        
        elif data.startswith("set_time_"):
            topic_name = data.replace("set_time_", "")
            if topic_name in self.work_topics:
                current_time = self.work_topics[topic_name].get('cleanup_time', '18:00')
                query.edit_message_text(
                    f"Тема: <b>{topic_name}</b>\n"
                    f"Текущее время очистки: {current_time}\n\n"
                    f"Введите новое время (формат ЧЧ:ММ):\n"
                    f"❌ Отмена - /cancel",
                    parse_mode='HTML'
                )
                context.user_data['waiting_topic_cleanup'] = topic_name
        
        elif data.startswith("clear_topic_"):
            topic_name = data.replace("clear_topic_", "")
            if topic_name in self.work_topics:
                message_count = len(self.work_topics[topic_name].get('messages', []))
                self.work_topics[topic_name]['messages'] = []
                self.save_data()
                query.edit_message_text(f"✅ Очищено {message_count} сообщений в теме '{topic_name}'!")
            else:
                query.edit_message_text("❌ Тема не найдена!")
            
            self.show_work_topics_menu(query)

    def handle_text_message(self, update, context):
        """Обработка текстовых сообщений"""
        user_data = context.user_data
        text = update.message.text
        
        # Проверка режима тишины - ТИХОЕ удаление без уведомлений
        if self.is_silent_time():
            try:
                update.message.delete()
                # НИКАКИХ УВЕДОМЛЕНИЙ - полная тишина
                logging.info("✅ Сообщение удалено в режиме тишины")
            except Exception as e:
                logging.error(f"Ошибка удаления сообщения: {e}")
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
                self.save_data()
                update.message.reply_text(f"✅ Время приветствия установлено на {text}")
                self.show_timers_menu_from_message(update)
                user_data.pop('waiting_welcome_time', None)
            else:
                update.message.reply_text("❌ Неверный формат времени! Используйте ЧЧ:ММ\nПример: 09:30\nПопробуйте еще раз:")
            return
        
        elif user_data.get('waiting_silent_start'):
            if self.validate_time(text):
                self.silent_start_time = text
                self.save_data()
                update.message.reply_text(f"✅ Начало тишины установлено на {text}")
                self.show_timers_menu_from_message(update)
                user_data.pop('waiting_silent_start', None)
            else:
                update.message.reply_text("❌ Неверный формат времени! Используйте ЧЧ:ММ\nПример: 22:30\nПопробуйте еще раз:")
            return
        
        elif user_data.get('waiting_silent_end'):
            if self.validate_time(text):
                self.silent_end_time = text
                self.save_data()
                update.message.reply_text(f"✅ Конец тишины установлено на {text}")
                self.show_timers_menu_from_message(update)
                user_data.pop('waiting_silent_end', None)
            else:
                update.message.reply_text("❌ Неверный формат времени! Используйте ЧЧ:ММ\nПример: 08:15\nПопробуйте еще раз:")
            return
        
        # Обработка приветствий
        elif user_data.get('waiting_daily_message'):
            try:
                if ":" in text:
                    day_part, message = text.split(":", 1)
                    day_part = day_part.strip().lower()
                    message = message.strip()
                    
                    days_map = {
                        "понедельник": "0", "пн": "0",
                        "вторник": "1", "вт": "1", 
                        "среда": "2", "ср": "2",
                        "четверг": "3", "чт": "3", 
                        "пятница": "4", "пт": "4",
                        "суббота": "5", "сб": "5",
                        "воскресенье": "6", "вс": "6"
                    }
                    
                    if day_part in days_map:
                        day_num = days_map[day_part]
                        day_name = self.get_day_name(day_num)
                        self.daily_messages[day_num] = message
                        self.save_data()
                        self.schedule_welcome_message()
                        
                        update.message.reply_text(
                            f"✅ Приветствие для {day_name} установлено!\n"
                            f"💬 Текст: {message}\n\n"
                            f"📝 Добавьте следующее приветствие или /cancel"
                        )
                    else:
                        update.message.reply_text(
                            "❌ Неверное название дня!\n"
                            "Доступно: Пн, Вт, Ср, Чт, Пт, Сб, Вс\n\n"
                            "Попробуйте еще раз:"
                        )
                else:
                    update.message.reply_text(
                        "❌ Неверный формат!\n"
                        "Используйте: День: Сообщение\n\n"
                        "Пример: Понедельник: Доброе утро!\n\n"
                        "Попробуйте еще раз:"
                    )
            
            except Exception as e:
                update.message.reply_text("❌ Ошибка! Попробуйте еще раз:")
            
            return

        # Обработка времени очистки для тем
        elif user_data.get('waiting_topic_cleanup'):
            topic_name = user_data['waiting_topic_cleanup']
            if self.validate_time(text):
                if topic_name in self.work_topics:
                    self.work_topics[topic_name]['cleanup_time'] = text
                    self.save_data()
                    self.schedule_topic_cleanup()
                    update.message.reply_text(f"✅ Время очистки для темы '{topic_name}' установлено на {text}")
                    self.show_work_topics_menu_from_message(update)
                else:
                    update.message.reply_text("❌ Тема не найдена!")
                user_data.pop('waiting_topic_cleanup', None)
            else:
                update.message.reply_text("❌ Неверный формат времени! Используйте ЧЧ:ММ\nПример: 18:30\nПопробуйте еще раз:")
            return

        # Обработка создания темы
        elif user_data.get('waiting_topic_name'):
            topic_name = text.strip()
            if topic_name:
                if topic_name not in self.work_topics:
                    self.work_topics[topic_name] = {
                        'created': datetime.now().strftime("%Y-%m-%d %H:%M"),
                        'messages': [],
                        'cleanup_time': "18:00"
                    }
                    self.save_data()
                    self.schedule_topic_cleanup()
                    update.message.reply_text(f"✅ Тема '{topic_name}' создана!\nВремя очистки по умолчанию: 18:00")
                else:
                    update.message.reply_text("❌ Тема с таким названием уже существует!")
            else:
                update.message.reply_text("❌ Название темы не может быть пустым!")
            
            user_data.pop('waiting_topic_name', None)

    def get_day_name(self, day_num):
        """Получить название дня по номеру"""
        days = {
            "0": "Понедельник",
            "1": "Вторник", 
            "2": "Среда",
            "3": "Четверг",
            "4": "Пятница",
            "5": "Суббота",
            "6": "Воскресеньe"
        }
        return days.get(day_num, "Неизвестный день")

    def validate_time(self, time_str):
        """Проверка корректности формата времени"""
        try:
            datetime.strptime(time_str, "%H:%M")
            return True
        except ValueError:
            return False

    def show_timers_menu_from_message(self, update):
        """Показать меню времени из текстового сообщения"""
        keyboard = [
            [InlineKeyboardButton(f"🕐 Приветствие: {self.welcome_time}", callback_data="timer_welcome")],
            [InlineKeyboardButton(f"🔇 Начало тишины: {self.silent_start_time}", callback_data="timer_silent_start")],
            [InlineKeyboardButton(f"🔊 Конец тишины: {self.silent_end_time}", callback_data="timer_silent_end")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text("⏰ Настройка времени:", reply_markup=reply_markup)

    def show_work_topics_menu_from_message(self, update):
        """Показать меню тем из текстового сообщения"""
        keyboard = [
            [InlineKeyboardButton("➕ Создать тему", callback_data="topic_create")],
            [InlineKeyboardButton("📋 Список тем", callback_data="topic_list")],
            [InlineKeyboardButton("⏰ Настройка времени очистки", callback_data="topic_timer")],
            [InlineKeyboardButton("🗑️ Очистить сообщения в теме", callback_data="topic_clear")],
            [InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text("💬 Управление рабочими темами:", reply_markup=reply_markup)

    def show_status(self, query):
        """Показать статус бота"""
        silent_status = "🔇 ВКЛЮЧЕН" if self.silent_mode else "🔊 ВЫКЛЮЧЕН"
        warning_status = "✅ ВКЛ" if self.silent_show_warning else "❌ ВЫКЛ"
        welcome_status = "👋 ВКЛЮЧЕН" if self.welcome_mode else "🚫 ВЫКЛЮЧЕН"
        
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        messages_info = ""
        
        filled_days = 0
        for i, day in enumerate(days):
            message = self.daily_messages.get(str(i), "❌ Не установлено")
            status = "✅" if str(i) in self.daily_messages else "❌"
            if str(i) in self.daily_messages:
                filled_days += 1
            messages_info += f"{status} <b>{day}:</b> {message}\n"
        
        topics_info = ""
        for topic_name, topic_data in self.work_topics.items():
            cleanup_time = topic_data.get('cleanup_time', '18:00')
            message_count = len(topic_data.get('messages', []))
            topics_info += f"• <b>{topic_name}</b> (очистка: {cleanup_time}, сообщений: {message_count})\n"
        
        topics_info = topics_info or "📭 Нет активных тем"
        
        text = (
            f"📊 <b>Статус бота</b>\n\n"
            f"🔇 <b>Режим тишины:</b> {silent_status}\n"
            f"⚠️ <b>Показ предупреждения:</b> {warning_status}\n"
            f"🕐 <b>Время тишины:</b> {self.silent_start_time} - {self.silent_end_time}\n"
            f"👋 <b>Приветствия:</b> {welcome_status}\n"
            f"🕐 <b>Время приветствия:</b> {self.welcome_time}\n"
            f"📅 <b>Заполнено дней:</b> {filled_days}/7\n\n"
            f"<b>Ежедневные приветствия:</b>\n{messages_info}\n"
            f"<b>Рабочие темы:</b>\n{topics_info}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)

def main():
    """Запуск бота"""
    bot = DailyMessageBot()
    
    # Создаем updater
    updater = Updater(token=bot.token, use_context=True)
    
    # Регистрация обработчиков
    updater.dispatcher.add_handler(CommandHandler("start", bot.start))
    updater.dispatcher.add_handler(CommandHandler("cancel", bot.start))
    updater.dispatcher.add_handler(CallbackQueryHandler(bot.button_handler))
    updater.dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, bot.handle_text_message))
    
    print("✅ Бот запущен на Render!")
    print("⏰ Время приветствия:", bot.welcome_time)
    print("🔇 Тишина:", bot.silent_start_time, "-", bot.silent_end_time, "Текущее:", current_time)


