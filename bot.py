from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telegram import Update
import asyncio

class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.application = None
        
    def setup(self):
        """Инициализация приложения и настройка обработчиков"""
        self.application = Application.builder().token(self.token).build()
        self._setup_handlers()
        return self

    def _setup_handlers(self):
        """Настройка всех обработчиков событий"""
        # Команды
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("register", self.register_topic))
        
        # Обработчики callback-запросов (нажатия кнопок)
        self.application.add_handler(CallbackQueryHandler(self.handle_callback_query))
        
        # Обработчики сообщений (важен порядок!)
        # 1. Групповые сообщения
        self.application.add_handler(
            MessageHandler(
                filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
                self.handle_group_message
            )
        )
        
        # 2. Личные сообщения (должен быть после групповых)
        self.application.add_handler(
            MessageHandler(
                filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
                self.handle_private_message
            )
        )

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /start"""
        await update.message.reply_text("Привет! Я бот...")

    async def register_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /register"""
        await update.message.reply_text("Регистрация темы...")

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик нажатий инлайн-кнопок"""
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text="Обработка кнопки...")

    async def handle_group_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик сообщений в группах"""
        # Пример: отвечаем только если бота упомянули
        if self.is_bot_mentioned(update):
            await update.message.reply_text("Ответ в группе")

    async def handle_private_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик личных сообщений"""
        await update.message.reply_text("Ответ в личке")

    def is_bot_mentioned(self, update: Update) -> bool:
        """Проверяет, упомянут ли бот в сообщении"""
        if not update.message or not update.message.text:
            return False
        return "@" + (self.application.bot.username or "") in update.message.text

    async def post_init(self, application: Application) -> None:
        """Пост-инициализационные действия"""
        print("Бот запущен!")
        await self.start_scheduler()

    async def start_scheduler(self):
        """Запуск асинхронного планировщика"""
        # Здесь может быть ваш планировщик
        print("Планировщик запущен")

    async def run(self):
        """Запуск бота"""
        if not self.application:
            self.setup()
            
        await self.application.run_polling(
            post_init=self.post_init,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True  # Очистка pending updates при запуске
        )


async def main():
    # Замените "YOUR_BOT_TOKEN" на реальный токен
    bot = TelegramBot("YOUR_BOT_TOKEN").setup()
    
    try:
        await bot.run()
    except Exception as e:
        print(f"Ошибка при запуске бота: {e}")


if __name__ == "__main__":
    asyncio.run(main())
