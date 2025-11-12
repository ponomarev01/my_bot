import logging
import json
import os
import pytz
import re
from datetime import datetime
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Конфигурация
# Вставьте свой токен сюда или используйте переменную окружения
TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ") 
DATA_FILE = "bot_data.json"

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Константы состояний ввода ---
INPUT_STATE_AUTO_DELETE = 'INPUT_AUTO_DELETE_TIME'
INPUT_STATE_AUTO_RESPONSE_KEY = 'INPUT_AUTO_RESPONSE_KEY'
INPUT_STATE_AUTO_RESPONSE_VALUE = 'INPUT_AUTO_RESPONSE_VALUE'
INPUT_STATE_DAILY_MESSAGE = 'INPUT_DAILY_MESSAGE'
INPUT_STATE_STOP_WORD = 'INPUT_STOP_WORD'
# ---------------------------------


class DailyMessageBot:
    
    DEFAULT_DATA = {
        'registered_topics': {},      # {chat_key: {chat_id, thread_id, name}}
        'auto_responses': {},         # {chat_key: {keyword: response}}
        'auto_delete_topics': {},     # {chat_key: {start_h, start_m, end_h, end_m}}
        'stop_words': {},             # {chat_key: [word1, word2, ...]}
        
        # Настройки приветствий
        'welcome_mode': False,
        'daily_messages': {},         # {day_index: message_text} (0=Mon, 6=Sun)
        'target_chat_id': None,
        'target_thread_id': None,
        'last_welcome_message': {},   # {chat_key: message_id}
    }

    def __init__(self, application: Application):
        self.application = application
        self.bot = application.bot
        self.data = self.load_data()
        
        # Инициализация атрибутов
        self.registered_topics = self.data.get('registered_topics', self.DEFAULT_DATA['registered_topics'])
        self.auto_responses = self.data.get('auto_responses', self.DEFAULT_DATA['auto_responses'])
        self.auto_delete_topics = self.data.get('auto_delete_topics', self.DEFAULT_DATA['auto_delete_topics'])
        self.stop_words = self.data.get('stop_words', self.DEFAULT_DATA['stop_words'])
        self.welcome_mode = self.data.get('welcome_mode', self.DEFAULT_DATA['welcome_mode'])
        self.daily_messages = self.data.get('daily_messages', self.DEFAULT_DATA['daily_messages'])
        self.target_chat_id = self.data.get('target_chat_id', self.DEFAULT_DATA['target_chat_id'])
        self.target_thread_id = self.data.get('target_thread_id', self.DEFAULT_DATA['target_thread_id'])
        self.last_welcome_message = self.data.get('last_welcome_message', self.DEFAULT_DATA['last_welcome_message'])
        
        self.scheduler = None 
        # Словарь для временного хранения callback_query для возврата в меню после ввода текста в ЛС
        self.last_query = {} 


    # --- Управление данными ---
        
    def load_data(self):
        """Загрузка данных из файла."""
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                try:
                    loaded_data = json.load(f)
                    return {**self.DEFAULT_DATA, **loaded_data}
                except json.JSONDecodeError:
                    logger.error("Ошибка декодирования JSON. Используются настройки по умолчанию.")
                    return self.DEFAULT_DATA
        return self.DEFAULT_DATA

    def save_data(self):
        """Сохранение данных в файл."""
        data_to_save = {
            'registered_topics': self.registered_topics,
            'auto_responses': self.auto_responses,
            'auto_delete_topics': self.auto_delete_topics,
            'stop_words': self.stop_words,
            'welcome_mode': self.welcome_mode,
            'daily_messages': self.daily_messages,
            'target_chat_id': self.target_chat_id,
            'target_thread_id': self.target_thread_id,
            'last_welcome_message': self.last_welcome_message,
        }
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)

    # --- Планировщик Приветствий ---

    def setup_schedulers(self):
        """Настройка и запуск планировщика."""
        # Используем существующий атрибут
        self.scheduler = AsyncIOScheduler(timezone=pytz.utc)
        
        hour = 9
        minute = 0
        
        self.scheduler.add_job(
            self.send_welcome_message_job, 
            'cron', 
            hour=hour, 
            minute=minute, 
            id='welcome_send', 
            replace_existing=True
        )
        
        self.scheduler.add_job(
            self.delete_welcome_message_job, 
            'cron', 
            hour=hour, 
            minute=minute + 5, 
            id='welcome_delete', 
            replace_existing=True
        )

        if not self.scheduler.running:
            self.scheduler.start()
            logger.info(f"Планировщик запущен. Отправка: {hour}:{minute} UTC, Удаление: {hour}:{minute+5} UTC.")
        return self.scheduler

    async def send_welcome_message_job(self):
        """Задача планировщика: Ежедневная отправка приветствия в целевую тему."""
        if not self.welcome_mode or not self.target_chat_id:
            return

        day_index = str(datetime.now(pytz.utc).weekday()) 
        message_text = self.daily_messages.get(day_index)
        
        if not message_text: return
            
        chat_id = self.target_chat_id
        thread_id = self.target_thread_id 
        chat_key = f"{chat_id}_{thread_id or 0}"
        
        try:
            sent_message = await self.bot.send_message(
                chat_id=chat_id, 
                text=message_text, 
                message_thread_id=thread_id if thread_id else None, 
                parse_mode='Markdown'
            )
            self.last_welcome_message[chat_key] = sent_message.message_id
            self.save_data()
        except Exception as e:
            logger.error(f"❌ Ошибка отправки приветствия: {e}")

    async def delete_welcome_message_job(self):
        """Задача планировщика: Ежедневное удаление ранее отправленного приветствия."""
        if not self.target_chat_id: return
            
        chat_id = self.target_chat_id
        thread_id = self.target_thread_id
        key = f"{chat_id}_{thread_id or 0}"
        
        message_id_to_delete = self.last_welcome_message.get(key)

        if message_id_to_delete:
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=message_id_to_delete)
                self.last_welcome_message.pop(key, None)
                self.save_data()
            except Exception as e:
                logger.warning(f"⚠️ Ошибка удаления сообщения: {e}")
                
    # --- Регистрация темы (Единственная команда в группе) ---

    async def register_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Единственная команда, которая должна быть введена в группе/теме для регистрации ID."""
        message = update.message
        if not message or message.chat.type == 'private':
            return await update.message.reply_text("❌ Эту команду нужно использовать в группе или теме.", quote=True)

        chat_id = str(message.chat.id)
        thread_id = message.message_thread_id
        
        if not thread_id and message.chat.type not in ['supergroup', 'group']:
            return await message.reply_text("❌ Используйте эту команду в теме или в супергруппе (форуме).")
        
        if thread_id:
            name = f"{message.chat.title} - Тема ID {thread_id}"
        else:
            name = f"Чат: {message.chat.title} (Главный поток)"
        
        key = f"{chat_id}_{thread_id or 0}"
        
        self.registered_topics[key] = {
            'chat_id': chat_id, 
            'thread_id': thread_id, 
            'name': name
        }
        self.save_data()
        
        await message.reply_text(
            f"✅ **Тема/Чат зарегистрирован!**\nТеперь вы можете настроить эту цель (`{name}`) в меню бота в ЛС (команда /start).", 
            parse_mode='Markdown', 
            quote=True
        )

    # --- Основной обработчик сообщений (ГРУППА) ---

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает сообщения в группах для Тихой Очистки, Стоп-слов и Авто-Ответов."""
        message = update.message
        if not message or not message.text or message.chat.type == 'private' or message.from_user.is_bot:
            return

        chat_id = str(message.chat.id)
        thread_id = message.message_thread_id 
        chat_key = f"{chat_id}_{thread_id or 0}"
        message_text = message.text.lower()
        
        # ----------------------------------------------------------------------
        # --- 1. ЛОГИКА ТИХОЙ АВТО-ОЧИСТКИ (Наивысший приоритет) ---
        # ----------------------------------------------------------------------
        
        delete_config = self.auto_delete_topics.get(chat_key)
        
        if delete_config:
            now_utc = datetime.now(pytz.utc).time()
            start_time = datetime(1, 1, 1, delete_config['start_h'], delete_config['start_m'], tzinfo=pytz.utc).time()
            end_time = datetime(1, 1, 1, delete_config['end_h'], delete_config['end_m'], tzinfo=pytz.utc).time()
            
            is_active = False
            if start_time < end_time:
                is_active = start_time <= now_utc < end_time
            else:
                is_active = now_utc >= start_time or now_utc < end_time
                    
            if is_active:
                try:
                    await message.delete()
                    logger.info(f"✅ Тихая Авто-Очистка: Удалено сообщение в чате {chat_id}, теме {thread_id or 'main'}.")
                    return 
                except Exception as e:
                    logger.warning(f"❌ Не удалось удалить сообщение (проверьте права): {e}")

        # ----------------------------------------------------------------------
        # --- 2. ЛОГИКА ЗАПРЕЩЕННЫХ СЛОВ (Средний приоритет) ---
        # ----------------------------------------------------------------------
        
        stop_words_list = self.stop_words.get(chat_key, [])
        if stop_words_list:
            for word in stop_words_list:
                # Проверяем нахождение слова целиком (добавляем пробелы для точности)
                word_pattern = rf"\b{re.escape(word.lower())}\b"
                if re.search(word_pattern, message_text):
                    try:
                        await message.delete()
                        logger.info(f"🚫 Запрещенное слово '{word}' найдено. Сообщение удалено в чате {chat_id}, теме {thread_id or 'main'}.")
                        return 
                    except Exception as e:
                        logger.warning(f"❌ Не удалось удалить сообщение с запрещенным словом (проверьте права): {e}")
                    break

        # ----------------------------------------------------------------------
        # --- 3. ЛОГИКА АВТО-ОТВЕТА (Низший приоритет) ---
        # ----------------------------------------------------------------------

        responses = self.auto_responses.get(chat_key, {})

        for keyword, response in responses.items():
            if keyword.lower() in message_text:
                try:
                    await message.reply_text(response, 
                                             message_thread_id=thread_id if thread_id else None,
                                             parse_mode='Markdown',
                                             quote=True)
                    logger.info(f"✅ Отправлен авто-ответ по ключевому слову '{keyword}' в чате {chat_id}, теме {thread_id or 'main'}.")
                    return
                except Exception as e:
                    logger.error(f"Ошибка отправки авто-ответа: {e}")
                    break
    
    # --- Обработчик текста в ЛС (для ввода значений) ---
    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода времени, текста или ключей в ЛС."""
        if update.message.chat.type != 'private': return
        
        state = context.user_data.get('state')
        
        if state == INPUT_STATE_DAILY_MESSAGE:
            await self._process_daily_message_input(update, context)
        elif state == INPUT_STATE_AUTO_DELETE:
            await self._process_autodelete_input(update, context)
        elif state == INPUT_STATE_AUTO_RESPONSE_KEY:
            await self._process_autoresponse_key_input(update, context)
        elif state == INPUT_STATE_AUTO_RESPONSE_VALUE:
            await self._process_autoresponse_value_input(update, context)
        elif state == INPUT_STATE_STOP_WORD:
            await self._process_stop_word_input(update, context)
        else:
            # Неизвестное состояние, возвращаем в главное меню
            await self._send_main_menu(update.message.chat_id, "⚠️ **Неизвестная команда.** Используйте меню:", clear_context=False)
            
    # --- Методы обработки ввода ---

    async def _process_daily_message_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        day_index = context.user_data.pop('day_index')
        new_message = update.message.text
        
        self.daily_messages[str(day_index)] = new_message
        self.save_data()
        
        day_name = self.get_day_name(day_index)
        
        query = self.last_query.get(update.message.chat_id)
        self._clear_user_data(context.user_data) 
        
        if query:
            # Возвращаемся в меню приветствий
            await self._edit_welcome_menu(query, f"✅ Текст для **{day_name}** сохранен!")
        else:
            # На всякий случай, если query был потерян
            await self._send_main_menu(update.message.chat_id, f"✅ Текст для **{day_name}** сохранен!", clear_context=True)


    async def _process_autodelete_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        time_str = update.message.text.strip()
        chat_key = context.user_data.pop('target_chat_key')
        
        if not re.match(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$", time_str):
            return await update.message.reply_text("❌ Неверный формат. Используйте HH:MM-HH:MM (например, 09:00-17:00).")

        try:
            start_str, end_str = time_str.split('-')
            start_h, start_m = map(int, start_str.split(':'))
            end_h, end_m = map(int, end_str.split(':'))
            
            if not (0 <= start_h <= 23 and 0 <= start_m <= 59 and 0 <= end_h <= 23 and 0 <= end_m <= 59):
                raise ValueError("Некорректные часы/минуты.")

        except ValueError:
            return await update.message.reply_text("❌ Некорректное время или формат. Проверьте, что HH:MM верны.")

        self.auto_delete_topics[chat_key] = {
            'start_h': start_h, 'start_m': start_m, 'end_h': end_h, 'end_m': end_m
        }
        self.save_data()
        
        topic_name = self.get_topic_name_by_key(chat_key)
        
        query = self.last_query.get(update.message.chat_id)
        self._clear_user_data(context.user_data)
        
        if query:
            # Возвращаемся в меню выбора темы авто-очистки
            await self._edit_autodelete_select_topic_menu(query, 
                f"✅ **Тихая Авто-Очистка** в `{topic_name}` настроена на {time_str} UTC.")
        else:
            await self._send_main_menu(update.message.chat_id, f"✅ **Авто-Очистка** настроена!", clear_context=True)


    async def _process_autoresponse_key_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        new_key = update.message.text.strip()
        if not new_key:
             return await update.message.reply_text("❌ Ключевое слово не может быть пустым. Введите слово или фразу.")
             
        context.user_data['temp_keyword'] = new_key
        context.user_data['state'] = INPUT_STATE_AUTO_RESPONSE_VALUE
        await update.message.reply_text("✍️ Отлично. Теперь **введите текст ответа**, который бот должен отправить (можно с Markdown).")


    async def _process_autoresponse_value_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        response_text = update.message.text
        keyword = context.user_data.pop('temp_keyword')
        chat_key = context.user_data.pop('target_chat_key')
        
        self.auto_responses.setdefault(chat_key, {})[keyword] = response_text
        self.save_data()
        
        topic_name = self.get_topic_name_by_key(chat_key)

        query = self.last_query.get(update.message.chat_id)
        self._clear_user_data(context.user_data)
        
        if query:
            # Возвращаемся в меню авто-ответов для конкретной темы
            await self._edit_autoresponse_menu(query, 
                chat_key, 
                status_message=f"✅ **Авто-Ответ** настроен в `{topic_name}`:\nСлово: `{keyword}`\nОтвет: `{response_text}`")
        else:
            await self._send_main_menu(update.message.chat_id, f"✅ **Авто-Ответ** настроен!", clear_context=True)

    async def _process_stop_word_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка ввода нового стоп-слова."""
        new_word = update.message.text.strip().lower()
        chat_key = context.user_data.pop('target_chat_key')
        
        if not new_word:
            return await update.message.reply_text("⚠️ Слово не может быть пустым. Введите слово.")

        self.stop_words.setdefault(chat_key, []).append(new_word)
        self.stop_words[chat_key] = sorted(list(set(self.stop_words[chat_key]))) 
        self.save_data()
        
        topic_name = self.get_topic_name_by_key(chat_key)

        query = self.last_query.get(update.message.chat_id)
        self._clear_user_data(context.user_data)
        
        if query:
            # Возвращаемся в меню стоп-слов для конкретной темы
            await self._edit_stop_word_menu(
                query, 
                chat_key, 
                status_message=f"✅ Слово **'{new_word}'** добавлено в `{topic_name}`."
            )
        else:
            await self._send_main_menu(update.message.chat_id, f"✅ **Стоп-слово** добавлено!", clear_context=True)


    # --- Утилиты ---

    def _clear_user_data(self, user_data):
        """Очищает все состояния ввода."""
        keys_to_clear = [
            'state', 'day_index', 'target_chat_key', 
            'temp_keyword', 'temp_response'
        ]
        for key in keys_to_clear:
            user_data.pop(key, None)

    def get_day_name(self, day_index: int) -> str:
        """Получает название дня недели по индексу (0-6)."""
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        return days[day_index]
        
    def get_topic_name_by_key(self, chat_key: str) -> str:
        # Улучшенная проверка на существование темы
        if chat_key in self.registered_topics:
             return self.registered_topics[chat_key].get('name', '❌ Тема не найдена')
        return '❌ Тема не найдена'

    # --- Обработчик команд и кнопок ---

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start: Сброс и отправка главного меню."""
        self._clear_user_data(context.user_data)
        
        # Очищаем сохраненный запрос, чтобы не вызвать сбой редактирования старого сообщения
        self.last_query.pop(update.message.chat_id, None) 
        
        await self._send_main_menu(update.message.chat_id, "👋 **Главное меню:**")

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        # Отвечаем на запрос сразу, чтобы избежать задержки и таймаута
        await query.answer() 
        data = query.data
        
        # Сохраняем последний запрос для возврата в меню после ввода текста в ЛС
        self.last_query[query.message.chat_id] = query
        self._clear_user_data(context.user_data) 
        
        try:
            if data == "back_main":
                await self._edit_main_menu(query)
            elif data.startswith("menu_"):
                if data == "menu_welcome":
                    await self._edit_welcome_menu(query)
                elif data == "menu_autodelete":
                    await self._edit_autodelete_select_topic_menu(query)
                elif data == "menu_autoresponse":
                    await self._edit_autoresponse_select_topic_menu(query)
                elif data == "menu_stop_words":
                    await self._edit_stop_word_select_topic_menu(query)

            # -------------------- ЕЖЕДНЕВНЫЕ ПРИВЕТСТВИЯ --------------------
            elif data.startswith("target_select_"):
                await self._edit_select_target_topic_menu(query)
            elif data.startswith("target_set_"):
                await self._action_set_target_topic(query, data.split("target_set_")[1])
            elif data.startswith("welcome_day_"):
                await self._handle_daily_message_setup(query, context, int(data.split("welcome_day_")[1]))
            elif data == "welcome_toggle":
                await self._action_toggle_welcome_mode(query)

            # -------------------- ТИХАЯ АВТО-ОЧИСТКА --------------------
            elif data.startswith("autodelete_select_"):
                await self._edit_autodelete_menu(query, data.split("autodelete_select_")[1])
            elif data.startswith("autodelete_set_"):
                await self._handle_autodelete_setup(query, context, data.split("autodelete_set_")[1])
            elif data.startswith("autodelete_remove_"):
                await self._action_remove_autodelete(query, data.split("autodelete_remove_")[1])

            # -------------------- АВТО-ОТВЕТЫ --------------------
            elif data.startswith("autoresponse_select_"):
                await self._edit_autoresponse_menu(query, data.split("autoresponse_select_")[1])
            elif data.startswith("autoresponse_add_"):
                await self._handle_autoresponse_setup(query, context, data.split("autoresponse_add_")[1])
            elif data.startswith("autoresponse_remove_"):
                parts = data.split("autoresponse_remove_")[1].split('|', 1)
                await self._action_remove_autoresponse(query, parts[0], parts[1])

            # -------------------- ЗАПРЕЩЕННЫЕ СЛОВА --------------------
            elif data.startswith("stop_select_"):
                await self._edit_stop_word_menu(query, data.split("stop_select_")[1])
            elif data.startswith("stop_add_"):
                await self._handle_stop_word_setup(query, context, data.split("stop_add_")[1])
            elif data.startswith("stop_remove_"):
                parts = data.split("stop_remove_")[1].split('|', 1)
                await self._action_remove_stop_word(query, parts[0], parts[1])
            
            else:
                 await query.edit_message_text("🚧 Неизвестная команда.", reply_markup=self._get_back_to_main_keyboard())
                 
        except Exception as e:
            # Универсальный обработчик сбоя
            logger.error(f"Критический сбой в обработчике Callback: {e}")
            await self._send_main_menu(query.message.chat_id, "❌ **Критическая ошибка навигации.** Пожалуйста, попробуйте снова.", clear_context=True)


    # --- Action Methods ---

    async def _action_set_target_topic(self, query: Update.callback_query, chat_key: str):
        """Устанавливает выбранную тему как цель для приветствий."""
        topic_data = self.registered_topics.get(chat_key)
        if not topic_data:
            return await query.edit_message_text("❌ Тема не найдена. Возможно, она была удалена.", reply_markup=self._get_back_to_main_keyboard())

        self.target_chat_id = topic_data['chat_id']
        self.target_thread_id = topic_data['thread_id']
        self.save_data()
        
        await self._edit_welcome_menu(query, f"✅ Цель для приветствий установлена: **{topic_data['name']}**")
        
    async def _action_toggle_welcome_mode(self, query: Update.callback_query):
        """Включает/выключает режим ежедневных приветствий."""
        self.welcome_mode = not self.welcome_mode
        self.save_data()
        await self._edit_welcome_menu(query)

    async def _action_remove_autodelete(self, query: Update.callback_query, chat_key: str):
        """Удаляет настройки Тихой Авто-Очистки."""
        if chat_key in self.auto_delete_topics:
            del self.auto_delete_topics[chat_key]
            self.save_data()
            topic_name = self.get_topic_name_by_key(chat_key)
            await self._edit_autodelete_select_topic_menu(query, f"❌ Очистка отключена для **{topic_name}**.")
        else:
             await self._edit_autodelete_select_topic_menu(query, "⚠️ Для этой темы не настроена очистка.")

    async def _action_remove_autoresponse(self, query: Update.callback_query, chat_key: str, keyword: str):
        """Удаляет один авто-ответ по ключевому слову."""
        if chat_key in self.auto_responses and keyword in self.auto_responses[chat_key]:
            del self.auto_responses[chat_key][keyword]
            if not self.auto_responses[chat_key]:
                del self.auto_responses[chat_key]
                
            self.save_data()
            topic_name = self.get_topic_name_by_key(chat_key)
            await self._edit_autoresponse_menu(query, chat_key, f"✅ Авто-ответ **'{keyword}'** удален из **{topic_name}**.")
        else:
             await self._edit_autoresponse_menu(query, chat_key, "❌ Авто-ответ не найден.")

    async def _action_remove_stop_word(self, query: Update.callback_query, chat_key: str, word_to_remove: str):
        """Удаляет одно стоп-слово."""
        words = self.stop_words.get(chat_key, [])
        if word_to_remove in words:
            words.remove(word_to_remove)
            if not words:
                del self.stop_words[chat_key]
                
            self.save_data()
            topic_name = self.get_topic_name_by_key(chat_key)
            await self._edit_stop_word_menu(query, chat_key, f"✅ Слово **'{word_to_remove}'** удалено из **{topic_name}**.")
        else:
             await self._edit_stop_word_menu(query, chat_key, "❌ Слово не найдено.")


    # --- Setup/Input Methods (Перевод в состояние ввода) ---

    async def _handle_daily_message_setup(self, query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, day_index: int):
        """Переводит бота в режим ожидания ввода текста для дня недели."""
        day_name = self.get_day_name(day_index)
        current_text = self.daily_messages.get(str(day_index), "_(Сообщение не задано)_")

        context.user_data['state'] = INPUT_STATE_DAILY_MESSAGE
        context.user_data['day_index'] = day_index
        
        prompt_text = (
            f"✍️ **Введите текст приветствия для {day_name}:**\n\n"
            f"Текущий текст:\n`{current_text}`\n\n"
            "_Введите новый текст. Поддерживается Markdown._"
        )
        await query.edit_message_text(prompt_text, parse_mode='Markdown')
        
    async def _handle_autodelete_setup(self, query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, chat_key: str):
        """Переводит бота в режим ожидания ввода времени для авто-очистки."""
        context.user_data['state'] = INPUT_STATE_AUTO_DELETE
        context.user_data['target_chat_key'] = chat_key
        
        config = self.auto_delete_topics.get(chat_key)
        current_time = "НЕТ"
        if config:
            start_str = f"{config['start_h']:02d}:{config['start_m']:02d}"
            end_str = f"{config['end_h']:02d}:{config['end_m']:02d}"
            current_time = f"{start_str}-{end_str} UTC"

        prompt_text = (
            "✍️ **Введите интервал Тихой Авто-Очистки (HH:MM-HH:MM UTC):**\n\n"
            "Пример: `09:00-17:00` (удалять сообщения с 9 утра до 5 вечера по UTC).\n"
            f"Текущий интервал: **{current_time}**\n\n"
            "_Чтобы отменить, введите /start_"
        )
        await query.edit_message_text(prompt_text, parse_mode='Markdown')
        
    async def _handle_autoresponse_setup(self, query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, chat_key: str):
        """Переводит бота в режим ожидания ввода ключевого слова."""
        context.user_data['state'] = INPUT_STATE_AUTO_RESPONSE_KEY
        context.user_data['target_chat_key'] = chat_key
        
        prompt_text = (
            "✍️ **Шаг 1 из 2: Введите ключевое слово или фразу**\n\n"
            "_Бот будет искать это слово в сообщении. Введите его (пример: хлеб, заказ, привет)._"
        )
        await query.edit_message_text(prompt_text, parse_mode='Markdown')

    async def _handle_stop_word_setup(self, query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, chat_key: str):
        """Переводит бота в режим ожидания ввода нового стоп-слова."""
        context.user_data['state'] = INPUT_STATE_STOP_WORD
        context.user_data['target_chat_key'] = chat_key
        
        prompt_text = (
            "✍️ **Введите Запрещенное Слово**\n\n"
            "_Бот будет искать это слово целиком. Например: 'мат', 'ссылка'._\n"
            "_Чтобы отменить, введите /start_"
        )
        await query.edit_message_text(prompt_text, parse_mode='Markdown')


    # --- Menu Building Methods ---

    def _get_back_to_main_keyboard(self):
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]])

    async def _send_main_menu(self, chat_id: int, text: str, clear_context: bool = True):
        """Отправляет Главное меню (используется после /start или сбоя)."""
        # Гарантируем очистку состояния при отправке нового меню
        if clear_context and chat_id in self.application.context_types.user_data: 
            self._clear_user_data(self.application.context_types.user_data[chat_id])
            
        keyboard = [
            [InlineKeyboardButton("🗓 Ежедневные Приветствия", callback_data="menu_welcome")],
            [InlineKeyboardButton("🗑️ Тихая Авто-Очистка", callback_data="menu_autodelete")],
            [InlineKeyboardButton("💬 Авто-Ответы", callback_data="menu_autoresponse")],
            [InlineKeyboardButton("🚫 Запрещенные Слова", callback_data="menu_stop_words")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # ----------------- Общий Статус -----------------
        status_text = "📊 **Общий Статус Бота**\n\n"
        
        status_text += f"🗓 **Приветствия:** {'ВКЛ ✅' if self.welcome_mode and self.target_chat_id else 'ВЫКЛ ❌'}\n"
        
        active_autodelete = sum(1 for k in self.registered_topics if k in self.auto_delete_topics)
        active_autoresponse = sum(1 for k in self.registered_topics if k in self.auto_responses)
        active_stopwords = sum(1 for k, v in self.stop_words.items() if v)

        status_text += f"🗑️ **Авто-Очистка:** {active_autodelete} тем\n"
        status_text += f"💬 **Авто-Ответы:** {active_autoresponse} тем\n"
        status_text += f"🚫 **Стоп-Слова:** {active_stopwords} тем\n\n"
        status_text += "---"
        # ------------------------------------------------

        await self.bot.send_message(chat_id, f"{status_text}\n\n{text}", reply_markup=reply_markup, parse_mode='Markdown')

    async def _edit_main_menu(self, query: Update.callback_query):
        """Редактирует сообщение до Главного меню. При сбое отправляет новое сообщение."""
        keyboard = [
            [InlineKeyboardButton("🗓 Ежедневные Приветствия", callback_data="menu_welcome")],
            [InlineKeyboardButton("🗑️ Тихая Авто-Очистка", callback_data="menu_autodelete")],
            [InlineKeyboardButton("💬 Авто-Ответы", callback_data="menu_autoresponse")],
            [InlineKeyboardButton("🚫 Запрещенные Слова", callback_data="menu_stop_words")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # ----------------- Общий Статус -----------------
        status_text = "📊 **Общий Статус Бота**\n\n"
        
        status_text += f"🗓 **Приветствия:** {'ВКЛ ✅' if self.welcome_mode and self.target_chat_id else 'ВЫКЛ ❌'}\n"
        
        active_autodelete = sum(1 for k in self.registered_topics if k in self.auto_delete_topics)
        active_autoresponse = sum(1 for k in self.registered_topics if k in self.auto_responses)
        active_stopwords = sum(1 for k, v in self.stop_words.items() if v)

        status_text += f"🗑️ **Авто-Очистка:** {active_autodelete} тем\n"
        status_text += f"💬 **Авто-Ответы:** {active_autoresponse} тем\n"
        status_text += f"🚫 **Стоп-Слова:** {active_stopwords} тем\n\n"
        status_text += "---"
        # ------------------------------------------------

        try: 
            await query.edit_message_text(f"{status_text}\n\n**Меню управления:**", reply_markup=reply_markup, parse_mode='Markdown')
        except Exception: 
            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Если редактирование не удалось, отправляем новое меню.
            logger.warning(f"Сбой редактирования главного меню (таймаут/старое сообщение). Отправка нового меню.")
            await self._send_main_menu(query.message.chat_id, "**Меню управления:**", clear_context=True)


    # --- Меню Приветствий ---

    async def _edit_welcome_menu(self, query: Update.callback_query, status_message: str = ""):
        """Меню для настройки ежедневных приветствий."""
        
        target_key = f"{self.target_chat_id}_{self.target_thread_id or 0}" if self.target_chat_id else None
        target_name = self.get_topic_name_by_key(target_key) if target_key in self.registered_topics else "❌ Не задана"
        status = "ВКЛЮЧЕНО ✅" if self.welcome_mode else "ВЫКЛЮЧЕНО ❌"

        day_buttons = []
        for i in range(7):
            day = self.get_day_name(i)
            status_day = "📝" if str(i) in self.daily_messages else "➕"
            day_buttons.append(InlineKeyboardButton(f"{status_day} {day}", callback_data=f"welcome_day_{i}"))
        
        keyboard = []
        for i in range(0, len(day_buttons), 2):
            row = [day_buttons[i]]
            if i + 1 < len(day_buttons): row.append(day_buttons[i+1])
            keyboard.append(row)

        keyboard.append([
            InlineKeyboardButton(f"🎯 Цель: {target_name}", callback_data="target_select_"),
            InlineKeyboardButton(f"▶️ Статус: {status}", callback_data="welcome_toggle")
        ])
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        
        message_text = (
            f"{status_message}\n\n" if status_message else ""
            "🗓 **Настройка Ежедневных Приветствий**\n\n"
            f"**Общий статус:** {status}\n"
            f"**Время (UTC):** 09:00 (Отправка) / 09:05 (Удаление)\n"
            f"**Цель:** {target_name}\n\n"
            "Нажмите на день, чтобы задать или изменить текст:"
        )
        try:
            await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception: 
            await self._edit_main_menu(query) # Если сбой, возвращаемся в главное меню


    async def _edit_select_target_topic_menu(self, query: Update.callback_query):
        """Меню выбора целевой темы для приветствий."""
        keyboard = []
        
        if not self.registered_topics:
            message_text = "❌ **Нет зарегистрированных тем.** Используйте `/register` в нужной теме в группе (форуме), чтобы она появилась здесь."
        else:
            message_text = "🎯 **Выберите целевую тему** для отправки ежедневных приветствий:"
            for key, data in self.registered_topics.items():
                is_selected = (self.target_chat_id == data.get('chat_id') and self.target_thread_id == data.get('thread_id'))
                status = "✅ Выбрано" if is_selected else ""
                keyboard.append([InlineKeyboardButton(f"{data['name']} {status}", callback_data=f"target_set_{key}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад к Приветствиям", callback_data="menu_welcome")])
        
        try:
            await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception:
            await self._edit_welcome_menu(query)
            
            
    # --- Меню Авто-Очистки ---

    async def _edit_autodelete_select_topic_menu(self, query: Update.callback_query, status_message: str = ""):
        """Меню выбора темы для настройки Тихой Авто-Очистки."""
        keyboard = []
        
        if not self.registered_topics:
            message_text = "❌ **Нет зарегистрированных тем.** Используйте `/register` в нужной теме в группе (форуме), чтобы она появилась здесь."
        else:
            message_text = f"{status_message}\n\n" if status_message else ""
            message_text += "🗑️ **Выберите тему** для настройки Тихой Авто-Очистки:"
            
            for key, data in self.registered_topics.items():
                status = "🕒" if key in self.auto_delete_topics else "➕"
                keyboard.append([InlineKeyboardButton(f"{status} {data['name']}", callback_data=f"autodelete_select_{key}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        
        try:
            await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception:
            await self._edit_main_menu(query) # Если сбой, возвращаемся в главное меню


    async def _edit_autodelete_menu(self, query: Update.callback_query, chat_key: str):
        """Меню настройки Тихой Авто-Очистки для выбранной темы."""
        topic_name = self.get_topic_name_by_key(chat_key)
        config = self.auto_delete_topics.get(chat_key)
        
        if topic_name == '❌ Тема не найдена':
            return await self._edit_autodelete_select_topic_menu(query, "❌ Тема не найдена. Возможно, она была удалена.")


        if config:
            start_str = f"{config['start_h']:02d}:{config['start_m']:02d}"
            end_str = f"{config['end_h']:02d}:{config['end_m']:02d}"
            status_text = f"**{start_str} - {end_str} UTC** (ВКЛЮЧЕНО ✅)"
            set_button = InlineKeyboardButton("📝 Изменить время", callback_data=f"autodelete_set_{chat_key}")
            remove_button = InlineKeyboardButton("❌ Отключить очистку", callback_data=f"autodelete_remove_{chat_key}")
        else:
            status_text = "**ОТКЛЮЧЕНО** ❌"
            set_button = InlineKeyboardButton("➕ Установить время очистки", callback_data=f"autodelete_set_{chat_key}")
            remove_button = None

        keyboard = [[set_button]]
        if remove_button: keyboard.append([remove_button])
        keyboard.append([InlineKeyboardButton("🔙 Назад к выбору темы", callback_data="menu_autodelete")])

        message_text = (
            f"🗑️ **Настройка Тихой Авто-Очистки**\n\n"
            f"**Тема:** `{topic_name}`\n"
            f"**Статус:** {status_text}\n\n"
            "Нажмите, чтобы задать интервал (HH:MM-HH:MM UTC), когда сообщения будут удаляться автоматически."
        )
        try:
            await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception:
            await self._edit_autodelete_select_topic_menu(query)

    # --- Меню Авто-Ответов ---
    
    async def _edit_autoresponse_select_topic_menu(self, query: Update.callback_query, status_message: str = ""):
        """Меню выбора темы для настройки Авто-Ответов."""
        keyboard = []
        
        if not self.registered_topics:
            message_text = "❌ **Нет зарегистрированных тем.** Используйте `/register` в нужной теме в группе (форуме), чтобы она появилась здесь."
        else:
            message_text = f"{status_message}\n\n" if status_message else ""
            message_text += "💬 **Выберите тему** для настройки Авто-Ответов:"
            
            for key, data in self.registered_topics.items():
                status = f"({len(self.auto_responses.get(key, {}))})" if key in self.auto_responses else "➕"
                keyboard.append([InlineKeyboardButton(f"{status} {data['name']}", callback_data=f"autoresponse_select_{key}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        
        try:
            await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception:
            await self._edit_main_menu(query) # Если сбой, возвращаемся в главное меню

    async def _edit_autoresponse_menu(self, query: Update.callback_query, chat_key: str, status_message: str = ""):
        """Меню настройки Авто-Ответов для выбранной темы."""
        topic_name = self.get_topic_name_by_key(chat_key)
        responses = self.auto_responses.get(chat_key, {})
        
        if topic_name == '❌ Тема не найдена':
            return await self._edit_autoresponse_select_topic_menu(query, "❌ Тема не найдена. Возможно, она была удалена.")


        keyboard = []
        
        # Кнопки для удаления
        if responses:
            for keyword, response in responses.items():
                # Обрезаем ответ для кнопки
                short_response = response[:20] + '...' if len(response) > 20 else response
                keyboard.append([InlineKeyboardButton(f"❌ '{keyword}' -> {short_response}", callback_data=f"autoresponse_remove_{chat_key}|{keyword}")])

        # Кнопка добавления и навигация
        keyboard.append([InlineKeyboardButton("➕ Добавить новый авто-ответ", callback_data=f"autoresponse_add_{chat_key}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад к выбору темы", callback_data="menu_autoresponse")])

        responses_list = "\n".join([f"- **{k}**: `{v}`" for k, v in responses.items()]) if responses else "Нет настроенных авто-ответов."

        message_text = (
            f"{status_message}\n\n" if status_message else ""
            f"💬 **Авто-Ответы** в `{topic_name}`\n\n"
            f"**Список:**\n{responses_list}"
        )
        try:
            await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception:
            await self._edit_autoresponse_select_topic_menu(query)
            
    # --- Меню Запрещенных Слов ---

    async def _edit_stop_word_select_topic_menu(self, query: Update.callback_query, status_message: str = ""):
        """Меню выбора темы для настройки Запрещенных Слов."""
        keyboard = []
        
        if not self.registered_topics:
            message_text = "❌ **Нет зарегистрированных тем.** Используйте `/register` в нужной теме в группе (форуме), чтобы она появилась здесь."
        else:
            message_text = f"{status_message}\n\n" if status_message else ""
            message_text += "🚫 **Выберите тему** для настройки Запрещенных Слов:"
            
            for key, data in self.registered_topics.items():
                status = f"({len(self.stop_words.get(key, []))})" if key in self.stop_words else "➕"
                keyboard.append([InlineKeyboardButton(f"{status} {data['name']}", callback_data=f"stop_select_{key}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")])
        
        try:
            await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception:
            await self._edit_main_menu(query) # Если сбой, возвращаемся в главное меню


    async def _edit_stop_word_menu(self, query: Update.callback_query, chat_key: str, status_message: str = ""):
        """Меню настройки Запрещенных Слов для выбранной темы."""
        topic_name = self.get_topic_name_by_key(chat_key)
        words = self.stop_words.get(chat_key, [])
        
        if topic_name == '❌ Тема не найдена':
            return await self._edit_stop_word_select_topic_menu(query, "❌ Тема не найдена. Возможно, она была удалена.")


        keyboard = []
        
        # Кнопки для удаления
        for word in words:
            keyboard.append([InlineKeyboardButton(f"❌ {word}", callback_data=f"stop_remove_{chat_key}|{word}")])

        # Кнопка добавления и навигация
        keyboard.append([InlineKeyboardButton("➕ Добавить новое слово", callback_data=f"stop_add_{chat_key}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад к выбору темы", callback_data="menu_stop_words")])

        words_list = "\n".join([f"- `{w}`" for w in words]) if words else "Нет настроенных запрещенных слов."

        message_text = (
            f"{status_message}\n\n" if status_message else ""
            f"🚫 **Запрещенные Слова** в `{topic_name}`\n\n"
            f"Сообщения, содержащие эти **целые** слова, будут удалены.\n\n"
            f"**Список:**\n{words_list}"
        )
        try:
            await query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception:
            await self._edit_stop_word_select_topic_menu(query)


# --- Точка входа ---

def main():
    """Запуск бота."""
    application = Application.builder().token(TOKEN).build()
    bot_instance = DailyMessageBot(application)

    # --- Хендлеры команд ---
    application.add_handler(CommandHandler("start", bot_instance.start_command)) 
    application.add_handler(CommandHandler("register", bot_instance.register_topic)) 
    
    # --- Обработчик нажатия кнопок ---
    application.add_handler(CallbackQueryHandler(bot_instance.handle_callback_query))

    # --- Обработчик сообщений (должен быть последним) ---
    # 1. Групповые сообщения (для авто-ответов, очистки, стоп-слов)
    application.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, bot_instance.handle_message))
    
    # 2. Личные сообщения (для ввода настроек)
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, bot_instance.handle_text_input)) 

    # --- ИСПРАВЛЕНИЕ: Запуск планировщика до run_polling (для старых версий) ---
    bot_instance.setup_schedulers()

    # Запуск
    logger.info("Бот запущен.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
