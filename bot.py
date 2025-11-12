# daily_bot.py
import logging
import json
import os
import re
from datetime import datetime, time as dt_time
import pytz
from typing import Optional, Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ---------------- Config ----------------
TOKEN = os.environ.get("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")
DATA_FILE = "bot_data.json"
MOSCOW = pytz.timezone("Europe/Moscow")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- input states ---
INPUT_STATE_AUTO_DELETE = "INPUT_AUTO_DELETE_TIME"
INPUT_STATE_AUTO_RESPONSE_KEY = "INPUT_AUTO_RESPONSE_KEY"
INPUT_STATE_AUTO_RESPONSE_VALUE = "INPUT_AUTO_RESPONSE_VALUE"
INPUT_STATE_DAILY_MESSAGE = "INPUT_DAILY_MESSAGE"
INPUT_STATE_STOP_WORD = "INPUT_STOP_WORD"
INPUT_STATE_WELCOME_TIME = "INPUT_WELCOME_TIME"
INPUT_STATE_WELCOME_DELETE_TIME = "INPUT_WELCOME_DELETE_TIME"
INPUT_STATE_SILENT_TIME = "INPUT_SILENT_TIME"
# ----------------------------------------

class DailyMessageBot:
    DEFAULT_DATA = {
        "registered_topics": {},      # {chat_key: {chat_id, thread_id, name}}
        "auto_responses": {},         # {chat_key: {keyword: response}}
        "auto_delete_topics": {},     # {chat_key: {start_h, start_m, end_h, end_m}}
        "silent_topics": {},          # {chat_key: {start_h, start_m, end_h, end_m}}
        "stop_words": {},             # {chat_key: [word1, ...]}
        "welcome_mode": False,
        "welcome_target": None,       # chat_key
        "welcome_send_time": {"hour": 9, "minute": 0},   # default Moscow time
        "welcome_delete_time": {"hour": 9, "minute": 5}, # default Moscow time
        "daily_messages": {},         # {day_index: message_text} (0=Mon)
        "last_welcome_message": {},   # {chat_key: message_id}
    }

    def __init__(self, application):
        self.application = application
        self.bot = application.bot
        self.data = self.load_data()

        # hydrate attributes
        d = self.data
        self.registered_topics = d.get("registered_topics", {})
        self.auto_responses = d.get("auto_responses", {})
        self.auto_delete_topics = d.get("auto_delete_topics", {})
        self.silent_topics = d.get("silent_topics", {})
        self.stop_words = d.get("stop_words", {})
        self.welcome_mode = d.get("welcome_mode", False)
        self.welcome_target = d.get("welcome_target", None)
        self.welcome_send_time = d.get("welcome_send_time", {"hour":9, "minute":0})
        self.welcome_delete_time = d.get("welcome_delete_time", {"hour":9, "minute":5})
        self.daily_messages = d.get("daily_messages", {})
        self.last_welcome_message = d.get("last_welcome_message", {})

        # scheduler for welcome send/delete
        self.scheduler = AsyncIOScheduler(timezone=MOSCOW)
        self.last_query: Dict[int, Any] = {}  # store last callback query by user chat_id
        self.setup_schedulers()

    # ----------------- Data -----------------
    def load_data(self):
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                try:
                    loaded = json.load(f)
                    merged = {**self.DEFAULT_DATA, **loaded}
                    return merged
                except json.JSONDecodeError:
                    logger.error("JSON decode error, loading defaults.")
                    return self.DEFAULT_DATA.copy()
        return self.DEFAULT_DATA.copy()

    def save_data(self):
        data_to_save = {
            "registered_topics": self.registered_topics,
            "auto_responses": self.auto_responses,
            "auto_delete_topics": self.auto_delete_topics,
            "silent_topics": self.silent_topics,
            "stop_words": self.stop_words,
            "welcome_mode": self.welcome_mode,
            "welcome_target": self.welcome_target,
            "welcome_send_time": self.welcome_send_time,
            "welcome_delete_time": self.welcome_delete_time,
            "daily_messages": self.daily_messages,
            "last_welcome_message": self.last_welcome_message,
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)

    # -------------- Scheduler --------------
    def setup_schedulers(self):
        # remove old jobs safely
        try:
            self.scheduler.remove_all_jobs()
        except Exception:
            pass

        # Job: send welcome messages daily at welcome_send_time
        h = self.welcome_send_time.get("hour", 9)
        m = self.welcome_send_time.get("minute", 0)
        self.scheduler.add_job(
            self.send_welcome_message_job,
            "cron",
            hour=h,
            minute=m,
            id="welcome_send",
            replace_existing=True,
        )
        # Job: delete welcome messages daily at welcome_delete_time
        dh = self.welcome_delete_time.get("hour", h)
        dm = self.welcome_delete_time.get("minute", (m + 5) % 60)
        self.scheduler.add_job(
            self.delete_welcome_message_job,
            "cron",
            hour=dh,
            minute=dm,
            id="welcome_delete",
            replace_existing=True,
        )
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler started (Moscow time).")

    async def send_welcome_message_job(self):
        if not self.welcome_mode or not self.welcome_target:
            return
        chat_key = self.welcome_target
        topic = self.registered_topics.get(chat_key)
        if not topic:
            logger.warning("Welcome target missing.")
            return
        day_index = str(datetime.now(MOSCOW).weekday())  # 0..6
        message_text = self.daily_messages.get(day_index)
        if not message_text:
            return
        chat_id = int(topic["chat_id"])
        thread_id = topic.get("thread_id")
        try:
            sent = await self.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                message_thread_id=thread_id if thread_id else None,
                parse_mode="Markdown",
            )
            self.last_welcome_message[chat_key] = sent.message_id
            self.save_data()
            logger.info(f"Sent welcome to {chat_key}")
        except Exception as e:
            logger.error(f"Error sending welcome: {e}")

    async def delete_welcome_message_job(self):
        if not self.welcome_target:
            return
        chat_key = self.welcome_target
        topic = self.registered_topics.get(chat_key)
        if not topic:
            return
        message_id = self.last_welcome_message.get(chat_key)
        if not message_id:
            return
        chat_id = int(topic["chat_id"])
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
            self.last_welcome_message.pop(chat_key, None)
            self.save_data()
            logger.info(f"Deleted welcome message {message_id} in {chat_key}")
        except Exception as e:
            logger.warning(f"Error deleting welcome message: {e}")

    # -------------- Util -------------------
    def _clear_user_data(self, user_data: dict):
        for k in ["state", "day_index", "target_chat_key", "temp_keyword", "temp_response"]:
            user_data.pop(k, None)

    def get_day_name(self, day_index: int) -> str:
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        return days[day_index]

    def get_topic_name_by_key(self, chat_key: str) -> str:
        topic = self.registered_topics.get(chat_key)
        if not topic:
            return "❌ Тема не найдена"
        return topic.get("name", "Без названия")

    async def is_user_admin(self, chat_id: int, user_id: int) -> bool:
        try:
            member = await self.bot.get_chat_member(chat_id, user_id)
            return member.status in ("administrator", "creator")
        except Exception:
            # fail safe: assume not admin to avoid skipping deletion when we can't check
            return False

    # ------------- Handlers ---------------
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Reset user state
        self._clear_user_data(context.user_data)
        # clear last query for this user to avoid edit conflicts
        if update.effective_chat:
            self.last_query.pop(update.effective_chat.id, None)
        await self._send_main_menu(update.effective_chat.id, "👋 **Главное меню:**")

    async def register_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # This command must be called inside group/topic
        message = update.message
        if not message or message.chat.type == "private":
            return await update.message.reply_text("❌ Эту команду нужно использовать в группе или теме.", quote=True)

        chat_id = str(message.chat.id)
        thread_id = message.message_thread_id
        if not thread_id and message.chat.type not in ["supergroup", "group"]:
            return await message.reply_text("❌ Используйте эту команду в теме или в супергруппе (форуме).")
        if thread_id:
            name = f"{message.chat.title} - Тема ID {thread_id}"
        else:
            name = f"Чат: {message.chat.title} (Главный поток)"
        key = f"{chat_id}_{thread_id or 0}"
        self.registered_topics[key] = {"chat_id": chat_id, "thread_id": thread_id, "name": name}
        self.save_data()
        await message.reply_text(
            f"✅ **Тема/Чат зарегистрирован!**\nТеперь вы можете настроить эту цель (`{name}`) в меню бота в ЛС (команда /start).",
            parse_mode="Markdown",
            quote=True,
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        if not message or not message.text:
            return
        if message.chat.type == "private" or message.from_user.is_bot:
            return

        chat_id = str(message.chat.id)
        thread_id = message.message_thread_id
        chat_key = f"{chat_id}_{thread_id or 0}"
        message_text = message.text.lower()

        # 1) Silent topics (highest priority) -- deletes everything in interval
        silent_config = self.silent_topics.get(chat_key)
        if silent_config:
            if self._time_in_interval_now(silent_config):
                try:
                    # delete without checks
                    await message.delete()
                    logger.info(f"Silent delete in {chat_key}")
                    return
                except Exception as e:
                    logger.warning(f"Silent delete failed: {e}")

        # 2) Auto-delete topics -- deletes only messages from non-admin users
        delete_config = self.auto_delete_topics.get(chat_key)
        if delete_config and self._time_in_interval_now(delete_config):
            try:
                is_admin = await self.is_user_admin(int(chat_id), message.from_user.id)
                if not is_admin:
                    await message.delete()
                    logger.info(f"Auto-delete removed message in {chat_key}")
                    return
            except Exception as e:
                logger.warning(f"Auto-delete check failed: {e}")

        # 3) Stop words (middle priority)
        stop_list = self.stop_words.get(chat_key, [])
        if stop_list:
            for w in stop_list:
                pattern = rf"\b{re.escape(w.lower())}\b"
                if re.search(pattern, message_text):
                    try:
                        await message.delete()
                        logger.info(f"Deleted message with stop word '{w}' in {chat_key}")
                        return
                    except Exception as e:
                        logger.warning(f"Failed to delete message with stop word: {e}")
                    break

        # 4) Auto-responses (lowest priority)
        responses = self.auto_responses.get(chat_key, {})
        for keyword, response in responses.items():
            if keyword.lower() in message_text:
                try:
                    await message.reply_text(
                        response,
                        message_thread_id=thread_id if thread_id else None,
                        parse_mode="Markdown",
                        quote=True,
                    )
                    logger.info(f"Auto-response for '{keyword}' in {chat_key}")
                    return
                except Exception as e:
                    logger.error(f"Error sending auto-response: {e}")
                    break

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        # save last query to be able to return after text input
        self.last_query[query.message.chat.id] = query
        self._clear_user_data(context.user_data)

        try:
            # main navigation
            if data == "back_main":
                await self._edit_main_menu(query)
                return
            if data == "menu_welcome":
                await self._edit_welcome_menu(query)
                return
            if data == "menu_autodelete":
                await self._edit_autodelete_select_topic_menu(query)
                return
            if data == "menu_autoresponse":
                await self._edit_autoresponse_select_topic_menu(query)
                return
            if data == "menu_stop_words":
                await self._edit_stop_word_select_topic_menu(query)
                return
            if data == "menu_silent":
                await self._edit_silent_select_topic_menu(query)
                return
            if data == "menu_status":
                await self._send_status_menu(query)
                return

            # Welcome: open select, set, toggle
            if data == "target_select":
                await self._edit_select_target_topic_menu(query)
                return
            if data.startswith("target_set_"):
                chat_key = data.split("target_set_")[1]
                await self._action_set_welcome_target(query, chat_key)
                return
            if data == "welcome_toggle":
                await self._action_toggle_welcome_mode(query)
                return
            if data.startswith("welcome_day_"):
                day_index = int(data.split("welcome_day_")[1])
                await self._handle_daily_message_setup(query, context, day_index)
                return
            if data == "welcome_time_set":
                await self._handle_welcome_time_setup(query, context)
                return
            if data == "welcome_delete_time_set":
                await self._handle_welcome_delete_time_setup(query, context)
                return

            # Auto-delete
            if data.startswith("autodelete_select_"):
                chat_key = data.split("autodelete_select_")[1]
                await self._edit_autodelete_menu(query, chat_key)
                return
            if data.startswith("autodelete_set_"):
                chat_key = data.split("autodelete_set_")[1]
                await self._handle_autodelete_setup(query, context, chat_key)
                return
            if data.startswith("autodelete_remove_"):
                chat_key = data.split("autodelete_remove_")[1]
                await self._action_remove_autodelete(query, chat_key)
                return

            # Silent mode
            if data.startswith("silent_select_"):
                chat_key = data.split("silent_select_")[1]
                await self._edit_silent_menu(query, chat_key)
                return
            if data.startswith("silent_set_"):
                chat_key = data.split("silent_set_")[1]
                await self._handle_silent_setup(query, context, chat_key)
                return
            if data.startswith("silent_remove_"):
                chat_key = data.split("silent_remove_")[1]
                await self._action_remove_silent(query, chat_key)
                return

            # Auto-responses
            if data.startswith("autoresponse_select_"):
                chat_key = data.split("autoresponse_select_")[1]
                await self._edit_autoresponse_menu(query, chat_key)
                return
            if data.startswith("autoresponse_add_"):
                chat_key = data.split("autoresponse_add_")[1]
                await self._handle_autoresponse_setup(query, context, chat_key)
                return
            if data.startswith("autoresponse_remove_"):
                payload = data.split("autoresponse_remove_")[1]
                chat_key, keyword = payload.split("|", 1)
                await self._action_remove_autoresponse(query, chat_key, keyword)
                return

            # Stop words
            if data.startswith("stop_select_"):
                chat_key = data.split("stop_select_")[1]
                await self._edit_stop_word_menu(query, chat_key)
                return
            if data.startswith("stop_add_"):
                chat_key = data.split("stop_add_")[1]
                await self._handle_stop_word_setup(query, context, chat_key)
                return
            if data.startswith("stop_remove_"):
                payload = data.split("stop_remove_")[1]
                chat_key, word = payload.split("|", 1)
                await self._action_remove_stop_word(query, chat_key, word)
                return

            # fallback
            await query.edit_message_text("🚧 Неизвестная команда.", reply_markup=self._get_back_to_main_keyboard())
        except Exception as e:
            logger.error(f"Critical callback handler error: {e}")
            try:
                await self._send_main_menu(query.message.chat.id, "❌ **Критическая ошибка.** Попробуйте снова.", clear_context=True)
            except Exception:
                pass

    # ---------- callback action implementations ----------
    async def _action_set_welcome_target(self, query, chat_key: str):
        topic = self.registered_topics.get(chat_key)
        if not topic:
            return await query.edit_message_text("❌ Тема не найдена.", reply_markup=self._get_back_to_main_keyboard())
        self.welcome_target = chat_key
        self.save_data()
        await self._edit_welcome_menu(query, f"✅ Цель для приветствий установлена: **{topic['name']}**")

    async def _action_toggle_welcome_mode(self, query):
        self.welcome_mode = not self.welcome_mode
        self.save_data()
        await self._edit_welcome_menu(query)

    async def _action_remove_autodelete(self, query, chat_key: str):
        if chat_key in self.auto_delete_topics:
            del self.auto_delete_topics[chat_key]
            self.save_data()
            await self._edit_autodelete_select_topic_menu(query, f"❌ Авто-очистка отключена для {self.get_topic_name_by_key(chat_key)}.")
        else:
            await self._edit_autodelete_select_topic_menu(query, "⚠️ Для этой темы не настроена очистка.")

    async def _action_remove_silent(self, query, chat_key: str):
        if chat_key in self.silent_topics:
            del self.silent_topics[chat_key]
            self.save_data()
            await self._edit_silent_select_topic_menu(query, f"❌ Тихий режим отключён для {self.get_topic_name_by_key(chat_key)}.")
        else:
            await self._edit_silent_select_topic_menu(query, "⚠️ Для этой темы не настроен Тихий режим.")

    async def _action_remove_autoresponse(self, query, chat_key: str, keyword: str):
        responses = self.auto_responses.get(chat_key, {})
        if keyword in responses:
            del responses[keyword]
            if not responses:
                self.auto_responses.pop(chat_key, None)
            self.save_data()
            await self._edit_autoresponse_menu(query, chat_key, f"✅ Авто-ответ '{keyword}' удалён.")
        else:
            await self._edit_autoresponse_menu(query, chat_key, "❌ Авто-ответ не найден.")

    async def _action_remove_stop_word(self, query, chat_key: str, word_to_remove: str):
        words = self.stop_words.get(chat_key, [])
        if word_to_remove in words:
            words.remove(word_to_remove)
            if not words:
                self.stop_words.pop(chat_key, None)
            self.save_data()
            await self._edit_stop_word_menu(query, chat_key, f"✅ Слово '{word_to_remove}' удалено.")
        else:
            await self._edit_stop_word_menu(query, chat_key, "❌ Слово не найдено.")

    # ---------- setups that put user into text input ----------
    async def _handle_daily_message_setup(self, query, context, day_index: int):
        # ask for daily message text
        current_text = self.daily_messages.get(str(day_index), "_(Сообщение не задано)_")
        context.user_data["state"] = INPUT_STATE_DAILY_MESSAGE
        context.user_data["day_index"] = day_index
        prompt = (
            f"✍️ **Введите текст приветствия для {self.get_day_name(day_index)}:**\n\n"
            f"Текущий текст:\n`{current_text}`\n\n"
            "_Введите новый текст. Поддерживается Markdown._"
        )
        await query.edit_message_text(prompt, parse_mode="Markdown")

    async def _handle_welcome_time_setup(self, query, context):
        context.user_data["state"] = INPUT_STATE_WELCOME_TIME
        prompt = (
            "✍️ **Введите время отправки для приветствий (HH:MM) в часовом поясе Europe/Moscow.**\n\n"
            "Пример: `09:00`"
        )
        await query.edit_message_text(prompt)

    async def _handle_welcome_delete_time_setup(self, query, context):
        context.user_data["state"] = INPUT_STATE_WELCOME_DELETE_TIME
        prompt = "✍️ **Введите время удаления приветствия (HH:MM) в Europe/Moscow.**\n\nПример: `09:05`"
        await query.edit_message_text(prompt)

    async def _handle_autodelete_setup(self, query, context, chat_key: str):
        context.user_data["state"] = INPUT_STATE_AUTO_DELETE
        context.user_data["target_chat_key"] = chat_key
        cfg = self.auto_delete_topics.get(chat_key)
        current = "НЕТ"
        if cfg:
            current = f"{cfg['start_h']:02d}:{cfg['start_m']:02d}-{cfg['end_h']:02d}:{cfg['end_m']:02d} (Moscow)"
        prompt = (
            "✍️ **Введите интервал Авто-Очистки (HH:MM-HH:MM) в Europe/Moscow:**\n\n"
            "Пример: `23:00-06:00` (будет удалять сообщения в этом интервале).\n"
            f"Текущий: {current}\n\nЧтобы отменить — введите /start"
        )
        await query.edit_message_text(prompt)

    async def _handle_silent_setup(self, query, context, chat_key: str):
        context.user_data["state"] = INPUT_STATE_SILENT_TIME
        context.user_data["target_chat_key"] = chat_key
        cfg = self.silent_topics.get(chat_key)
        current = "НЕТ"
        if cfg:
            current = f"{cfg['start_h']:02d}:{cfg['start_m']:02d}-{cfg['end_h']:02d}:{cfg['end_m']:02d} (Moscow)"
        prompt = (
            "✍️ **Введите интервал Тихого Режима (HH:MM-HH:MM) в Europe/Moscow:**\n\n"
            "В Тихом режиме бот будет **мгновенно удалять все сообщения** без исключений.\n"
            f"Текущий: {current}\n\nЧтобы отменить — введите /start"
        )
        await query.edit_message_text(prompt)

    async def _handle_autoresponse_setup(self, query, context, chat_key: str):
        context.user_data["state"] = INPUT_STATE_AUTO_RESPONSE_KEY
        context.user_data["target_chat_key"] = chat_key
        prompt = "✍️ **Шаг 1/2: Введите ключевое слово или фразу** (пример: 'булка', 'заказ')"
        await query.edit_message_text(prompt)

    async def _handle_stop_word_setup(self, query, context, chat_key: str):
        context.user_data["state"] = INPUT_STATE_STOP_WORD
        context.user_data["target_chat_key"] = chat_key
        prompt = "✍️ **Введите Запрещенное Слово** (будет удаляться при обнаружении целиком)\n\nЧтобы отменить — /start"
        await query.edit_message_text(prompt)

    # ------------- Text input processing --------------
    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message.chat.type != "private":
            return
        state = context.user_data.get("state")
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
        elif state == INPUT_STATE_WELCOME_TIME:
            await self._process_welcome_time_input(update, context)
        elif state == INPUT_STATE_WELCOME_DELETE_TIME:
            await self._process_welcome_delete_time_input(update, context)
        elif state == INPUT_STATE_SILENT_TIME:
            await self._process_silent_input(update, context)
        else:
            await self._send_main_menu(update.message.chat.id, "⚠️ Неизвестная команда. Меню:", clear_context=False)

    async def _process_daily_message_input(self, update, context):
        day_index = context.user_data.pop("day_index")
        new_message = update.message.text
        self.daily_messages[str(day_index)] = new_message
        self.save_data()
        day_name = self.get_day_name(day_index)
        query = self.last_query.get(update.message.chat.id)
        self._clear_user_data(context.user_data)
        if query:
            await self._edit_welcome_menu(query, f"✅ Текст для **{day_name}** сохранен!")
        else:
            await self._send_main_menu(update.message.chat.id, f"✅ Текст для **{day_name}** сохранен!", clear_context=True)

    async def _process_autodelete_input(self, update, context):
        time_str = update.message.text.strip()
        chat_key = context.user_data.pop("target_chat_key")
        if not re.match(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$", time_str):
            return await update.message.reply_text("❌ Неверный формат. Используйте HH:MM-HH:MM (например, 09:00-17:00).")
        try:
            start_str, end_str = time_str.split("-")
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))
            if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
                raise ValueError
        except Exception:
            return await update.message.reply_text("❌ Некорректное время или формат.")
        self.auto_delete_topics[chat_key] = {"start_h": sh, "start_m": sm, "end_h": eh, "end_m": em}
        self.save_data()
        query = self.last_query.get(update.message.chat.id)
        self._clear_user_data(context.user_data)
        if query:
            await self._edit_autodelete_menu(query, chat_key, f"✅ Авто-Очистка настроена: {time_str} (Moscow)")
        else:
            await self._send_main_menu(update.message.chat.id, "✅ Авто-Очистка настроена!", clear_context=True)

    async def _process_silent_input(self, update, context):
        time_str = update.message.text.strip()
        chat_key = context.user_data.pop("target_chat_key")
        if not re.match(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$", time_str):
            return await update.message.reply_text("❌ Неверный формат. Используйте HH:MM-HH:MM.")
        try:
            start_str, end_str = time_str.split("-")
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))
            if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
                raise ValueError
        except Exception:
            return await update.message.reply_text("❌ Некорректное время или формат.")
        self.silent_topics[chat_key] = {"start_h": sh, "start_m": sm, "end_h": eh, "end_m": em}
        self.save_data()
        query = self.last_query.get(update.message.chat.id)
        self._clear_user_data(context.user_data)
        if query:
            await self._edit_silent_menu(query, chat_key, f"✅ Тихий режим настроен: {time_str} (Moscow)")
        else:
            await self._send_main_menu(update.message.chat.id, "✅ Тихий режим настроен!", clear_context=True)

    async def _process_autoresponse_key_input(self, update, context):
        new_key = update.message.text.strip()
        if not new_key:
            return await update.message.reply_text("❌ Ключ не может быть пустым.")
        context.user_data["temp_keyword"] = new_key
        context.user_data["state"] = INPUT_STATE_AUTO_RESPONSE_VALUE
        await update.message.reply_text("✍️ Теперь введите текст ответа (шаг 2 из 2).")

    async def _process_autoresponse_value_input(self, update, context):
        response_text = update.message.text
        keyword = context.user_data.pop("temp_keyword")
        chat_key = context.user_data.pop("target_chat_key")
        self.auto_responses.setdefault(chat_key, {})[keyword] = response_text
        self.save_data()
        self._clear_user_data(context.user_data)
        query = self.last_query.get(update.message.chat.id)
        if query:
            await self._edit_autoresponse_menu(query, chat_key, f"✅ Авто-ответ настроен:\nСлово: `{keyword}`\nОтвет: `{response_text}`")
        else:
            await self._send_main_menu(update.message.chat.id, "✅ Авто-ответ настроен!", clear_context=True)

    async def _process_stop_word_input(self, update, context):
        new_word = update.message.text.strip().lower()
        chat_key = context.user_data.pop("target_chat_key")
        if not new_word:
            return await update.message.reply_text("⚠️ Слово не может быть пустым.")
        self.stop_words.setdefault(chat_key, []).append(new_word)
        # unique & sort
        self.stop_words[chat_key] = sorted(list(set(self.stop_words[chat_key])))
        self.save_data()
        self._clear_user_data(context.user_data)
        query = self.last_query.get(update.message.chat.id)
        if query:
            await self._edit_stop_word_menu(query, chat_key, f"✅ Слово '{new_word}' добавлено.")
        else:
            await self._send_main_menu(update.message.chat.id, "✅ Стоп-слово добавлено!", clear_context=True)

    async def _process_welcome_time_input(self, update, context):
        t = update.message.text.strip()
        if not re.match(r"^\d{1,2}:\d{2}$", t):
            return await update.message.reply_text("❌ Неверный формат. Используйте HH:MM.")
        h, m = map(int, t.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return await update.message.reply_text("❌ Часы/минуты вне диапазона.")
        self.welcome_send_time = {"hour": h, "minute": m}
        self.save_data()
        # recreate scheduler jobs
        self.setup_schedulers()
        self._clear_user_data(context.user_data)
        query = self.last_query.get(update.message.chat.id)
        if query:
            await self._edit_welcome_menu(query, f"✅ Время отправки установлено на {h:02d}:{m:02d} Moscow.")
        else:
            await self._send_main_menu(update.message.chat.id, "✅ Время отправки установлено.", clear_context=True)

    async def _process_welcome_delete_time_input(self, update, context):
        t = update.message.text.strip()
        if not re.match(r"^\d{1,2}:\d{2}$", t):
            return await update.message.reply_text("❌ Неверный формат. Используйте HH:MM.")
        h, m = map(int, t.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return await update.message.reply_text("❌ Часы/минуты вне диапазона.")
        self.welcome_delete_time = {"hour": h, "minute": m}
        self.save_data()
        self.setup_schedulers()
        self._clear_user_data(context.user_data)
        query = self.last_query.get(update.message.chat.id)
        if query:
            await self._edit_welcome_menu(query, f"✅ Время удаления установлено на {h:02d}:{m:02d} Moscow.")
        else:
            await self._send_main_menu(update.message.chat.id, "✅ Время удаления установлено.", clear_context=True)

    # ---------- Menu builders and editors ----------
    def _get_back_to_main_keyboard(self):
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад в главное меню", callback_data="back_main")]])

    async def _send_main_menu(self, chat_id: int, text: str, clear_context: bool = True):
        keyboard = [
            [InlineKeyboardButton("🗓 Ежедневные Приветствия", callback_data="menu_welcome")],
            [
                InlineKeyboardButton("🗑️ Тихая Авто-Очистка", callback_data="menu_autodelete"),
                InlineKeyboardButton("🔕 Тихий режим", callback_data="menu_silent"),
            ],
            [
                InlineKeyboardButton("💬 Авто-Ответы", callback_data="menu_autoresponse"),
                InlineKeyboardButton("🚫 Запрещенные Слова", callback_data="menu_stop_words"),
            ],
            [InlineKeyboardButton("📊 Статус", callback_data="menu_status")],
        ]
        status_short = (
            f"Приветы: {'ВКЛ' if self.welcome_mode else 'ВЫКЛ'} | "
            f"Авто-очистка тем: {len(self.auto_delete_topics)} | "
            f"Тихий режим тем: {len(self.silent_topics)} | "
            f"Авто-ответы тем: {len(self.auto_responses)}"
        )
        full_text = f"{text}\n\n{status_short}"
        try:
            await self.application.bot.send_message(chat_id=chat_id, text=full_text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Failed to send main menu: {e}")

    async def _edit_main_menu(self, query):
        text = "👋 **Главное меню:**"
        keyboard = [
            [InlineKeyboardButton("🗓 Ежедневные Приветствия", callback_data="menu_welcome")],
            [
                InlineKeyboardButton("🗑️ Тихая Авто-Очистка", callback_data="menu_autodelete"),
                InlineKeyboardButton("🔕 Тихий режим", callback_data="menu_silent"),
            ],
            [
                InlineKeyboardButton("💬 Авто-Ответы", callback_data="menu_autoresponse"),
                InlineKeyboardButton("🚫 Запрещенные Слова", callback_data="menu_stop_words"),
            ],
            [InlineKeyboardButton("📊 Статус", callback_data="menu_status")],
        ]
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Edit main menu failed: {e}")

    async def _edit_welcome_menu(self, query, status_message: Optional[str] = None):
        lines = []
        lines.append("🗓 **Ежедневные Приветствия**")
        lines.append(f"Режим: {'ВКЛ ✅' if self.welcome_mode else 'ВЫКЛ ❌'}")
        if self.welcome_target:
            lines.append(f"Цель: {self.get_topic_name_by_key(self.welcome_target)}")
        else:
            lines.append("Цель: ❌ Не выбрана")
        st = self.welcome_send_time
        dt = self.welcome_delete_time
        lines.append(f"Время отправки: {st['hour']:02d}:{st['minute']:02d} Moscow")
        lines.append(f"Время удаления: {dt['hour']:02d}:{dt['minute']:02d} Moscow")
        text = "\n".join(lines)
        if status_message:
            text = status_message + "\n\n" + text

        kb = []
        # open target selection
        if self.registered_topics:
            kb.append([InlineKeyboardButton("📌 Выбрать цель приветствий", callback_data="target_select")])
        else:
            kb.append([InlineKeyboardButton("📌 Топики не зарегистрированы", callback_data="back_main")])
        kb.append([InlineKeyboardButton("🔀 Включить/Выключить", callback_data="welcome_toggle")])
        kb.append([
            InlineKeyboardButton("⏰ Установить время отправки", callback_data="welcome_time_set"),
            InlineKeyboardButton("🗑 Установить время удаления", callback_data="welcome_delete_time_set"),
        ])

        # per-day buttons (7)
        row = []
        for i in range(7):
            row.append(InlineKeyboardButton(self.get_day_name(i)[:3], callback_data=f"welcome_day_{i}"))
            if (i + 1) % 4 == 0:
                kb.append(row)
                row = []
        if row:
            kb.append(row)

        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])

        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"Edit welcome menu failed: {e}")

    async def _edit_select_target_topic_menu(self, query):
        kb = []
        for key, t in self.registered_topics.items():
            kb.append([InlineKeyboardButton(t["name"], callback_data=f"target_set_{key}")])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="menu_welcome")])
        await query.edit_message_text("Выберите тему для приветствий:", reply_markup=InlineKeyboardMarkup(kb))

    async def _edit_autodelete_select_topic_menu(self, query, status_message: Optional[str] = None):
        kb = []
        if self.registered_topics:
            for key, t in self.registered_topics.items():
                kb.append([InlineKeyboardButton(t["name"], callback_data=f"autodelete_select_{key}")])
        else:
            kb.append([InlineKeyboardButton("Нет зарегистрированных тем", callback_data="back_main")])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])
        text = "🗑️ **Тихая Авто-Очистка**\n\nВыберите тему:" 
        if status_message:
            text = status_message + "\n\n" + text
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    async def _edit_autodelete_menu(self, query, chat_key: str, status_message: Optional[str] = None):
        topic_name = self.get_topic_name_by_key(chat_key)
        cfg = self.auto_delete_topics.get(chat_key)
        cfg_text = "Нет"
        if cfg:
            cfg_text = f"{cfg['start_h']:02d}:{cfg['start_m']:02d}-{cfg['end_h']:02d}:{cfg['end_m']:02d} Moscow"
        text = f"🗑️ Авто-Очистка — {topic_name}\n\nТекущее: {cfg_text}"
        if status_message:
            text = status_message + "\n\n" + text
        kb = [
            [InlineKeyboardButton("✏️ Установить интервал", callback_data=f"autodelete_set_{chat_key}")],
            [InlineKeyboardButton("❌ Отключить", callback_data=f"autodelete_remove_{chat_key}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_autodelete")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    async def _edit_silent_select_topic_menu(self, query, status_message: Optional[str] = None):
        kb = []
        if self.registered_topics:
            for key, t in self.registered_topics.items():
                kb.append([InlineKeyboardButton(t["name"], callback_data=f"silent_select_{key}")])
        else:
            kb.append([InlineKeyboardButton("Нет зарегистрированных тем", callback_data="back_main")])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])
        text = "🔕 **Тихий Режим**\n\nВыберите тему:"
        if status_message:
            text = status_message + "\n\n" + text
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    async def _edit_silent_menu(self, query, chat_key: str, status_message: Optional[str] = None):
        topic_name = self.get_topic_name_by_key(chat_key)
        cfg = self.silent_topics.get(chat_key)
        cfg_text = "Нет"
        if cfg:
            cfg_text = f"{cfg['start_h']:02d}:{cfg['start_m']:02d}-{cfg['end_h']:02d}:{cfg['end_m']:02d} Moscow"
        text = f"🔕 Тихий Режим — {topic_name}\n\nТекущий: {cfg_text}\n\n(в тишине бот удаляет ВСЕ сообщения)"
        if status_message:
            text = status_message + "\n\n" + text
        kb = [
            [InlineKeyboardButton("✏️ Установить интервал тишины", callback_data=f"silent_set_{chat_key}")],
            [InlineKeyboardButton("❌ Отключить тишину", callback_data=f"silent_remove_{chat_key}")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_silent")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    async def _edit_autoresponse_select_topic_menu(self, query, status_message: Optional[str] = None):
        kb = []
        if self.registered_topics:
            for key, t in self.registered_topics.items():
                kb.append([InlineKeyboardButton(t["name"], callback_data=f"autoresponse_select_{key}")])
        else:
            kb.append([InlineKeyboardButton("Нет зарегистрированных тем", callback_data="back_main")])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])
        text = "💬 **Авто-Ответы**\n\nВыберите тему:"
        if status_message:
            text = status_message + "\n\n" + text
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    async def _edit_autoresponse_menu(self, query, chat_key: str, status_message: Optional[str] = None):
        topic_name = self.get_topic_name_by_key(chat_key)
        responses = self.auto_responses.get(chat_key, {})
        resp_lines = []
        if responses:
            for k in responses:
                resp_lines.append(f"`{k}`")
        else:
            resp_lines.append("_Авто-ответов нет_")
        text = f"💬 Авто-Ответы — {topic_name}\n\nСлова: {', '.join(resp_lines)}"
        if status_message:
            text = status_message + "\n\n" + text
        kb = [
            [InlineKeyboardButton("➕ Добавить авто-ответ", callback_data=f"autoresponse_add_{chat_key}")],
        ]
        if responses:
            for k in list(responses.keys()):
                kb.append([InlineKeyboardButton(f"❌ Удалить '{k}'", callback_data=f"autoresponse_remove_{chat_key}|{k}")])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="menu_autoresponse")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    async def _edit_stop_word_select_topic_menu(self, query, status_message: Optional[str] = None):
        kb = []
        if self.registered_topics:
            for key, t in self.registered_topics.items():
                kb.append([InlineKeyboardButton(t["name"], callback_data=f"stop_select_{key}")])
        else:
            kb.append([InlineKeyboardButton("Нет зарегистрированных тем", callback_data="back_main")])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="back_main")])
        text = "🚫 **Запрещенные Слова**\n\nВыберите тему:"
        if status_message:
            text = status_message + "\n\n" + text
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    async def _edit_stop_word_menu(self, query, chat_key: str, status_message: Optional[str] = None):
        topic_name = self.get_topic_name_by_key(chat_key)
        words = self.stop_words.get(chat_key, [])
        text = f"🚫 Запрещенные слова — {topic_name}\n\n"
        if words:
            text += "Слова:\n" + ", ".join(f"`{w}`" for w in words)
        else:
            text += "_Список пуст_"
        if status_message:
            text = status_message + "\n\n" + text
        kb = [
            [InlineKeyboardButton("➕ Добавить стоп-слово", callback_data=f"stop_add_{chat_key}")],
        ]
        if words:
            for w in words:
                kb.append([InlineKeyboardButton(f"❌ Удалить '{w}'", callback_data=f"stop_remove_{chat_key}|{w}")])
        kb.append([InlineKeyboardButton("🔙 Назад", callback_data="menu_stop_words")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

    async def _send_status_menu(self, query_or_update):
        # Accept both Query and Update
        if hasattr(query_or_update, "edit_message_text"):
            edit = True
            query = query_or_update
        else:
            edit = False
            update = query_or_update
        lines = []
        lines.append("📊 **Статус бота**")
        lines.append(f"Приветствия: {'ВКЛ' if self.welcome_mode else 'ВЫКЛ'}")
        if self.welcome_target:
            lines.append(f"Цель приветствий: {self.get_topic_name_by_key(self.welcome_target)}")
        lines.append(f"Авто-очистка включена для {len(self.auto_delete_topics)} тем")
        lines.append(f"Тихий режим включен для {len(self.silent_topics)} тем")
        total_responses = sum(len(v) for v in self.auto_responses.values()) if self.auto_responses else 0
        lines.append(f"Авто-ответов: {total_responses}")
        total_stop_words = sum(len(v) for v in self.stop_words.values()) if self.stop_words else 0
        lines.append(f"Стоп-слов: {total_stop_words}")
        text = "\n".join(lines)
        kb = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
        if edit:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    # ---------- helper ----------
    def _time_in_interval_now(self, cfg: dict) -> bool:
        # cfg: {start_h, start_m, end_h, end_m} in Moscow timezone
        now = datetime.now(MOSCOW).time()
        start = dt_time(cfg["start_h"], cfg["start_m"])
        end = dt_time(cfg["end_h"], cfg["end_m"])
        if start < end:
            return start <= now < end
        else:
            # overnight
            return now >= start or now < end

# ---------------- Main setup ----------------
def main():
    application = ApplicationBuilder().token(TOKEN).concurrent_updates(True).build()

    bot = DailyMessageBot(application)

    # Command handlers
    application.add_handler(CommandHandler("start", bot.start_command))
    application.add_handler(CommandHandler("register_topic", bot.register_topic))

    # Callback queries
    application.add_handler(CallbackQueryHandler(bot.handle_callback_query))

    # Message handlers
    # Group messages
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), bot.handle_message))
    # Private text input handler (ensure it runs before the generic text handler if needed)
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & (~filters.COMMAND), bot.handle_text_input))

    # Start
    logger.info("Starting bot...")
    application.run_polling()

if __name__ == "__main__":
    main()
