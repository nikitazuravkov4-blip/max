
import json
import logging
import os
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# --- КОНСТАНТЫ ---
# Замените на токен вашего бота, полученный от @BotFather
BOT_TOKEN = "8366121500:AAGViKRhJT_fUXjJuiKOZEaJT7WNpwrotU4" 
# Замените на числовой ID вашего канала (например, -1001234567890)
CHANNEL_ID = -1002924596101 
# Замените на имя вашего канала без @ (только для ссылок)
CHANNEL_USERNAME = "meltedtut" 

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)  # Use __name__ for logger name

# --- Вспомогательные функции для обработки JSON ---

def process_json_data(json_bytes: bytes):
    """
    Обрабатывает JSON-данные из байтов, удаляя слои с ind=12345679.
    Возвращает обработанные данные и флаг, было ли что-то удалено.
    """
    try:
        data = json.loads(json_bytes.decode('utf-8'))
    except json.JSONDecodeError:
        raise ValueError("Некорректный JSON файл.")

    found_watermark = False
    if 'layers' in data:
        original_length = len(data['layers'])
        # Фильтруем слои, удаляя те, у которых 'ind' равен 12345679
        data['layers'] = [layer for layer in data['layers'] if layer.get('ind') != 12345679]
        new_length = len(data['layers'])
        
        if original_length != new_length:
            found_watermark = True
            logger.info(f"Удалено {original_length - new_length} слоев-водяных знаков.")
        else:
            logger.info("Водяные знаки (ind=12345679) не найдены.")

    return data, found_watermark

def write_json_data(data, compress=True) -> bytes:
    """
    Преобразует Python-объект в JSON-строку (байты), с опцией сжатия.
    """
    output_buffer = BytesIO()
    if compress:
        # Сжатый режим: без отступов, без лишних пробелов
        json_string = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    else:
        # Форматированный режим: с отступами
        json_string = json.dumps(data, indent=4, ensure_ascii=False)
    
    output_buffer.write(json_string.encode('utf-8'))
    output_buffer.seek(0) # Перемещаем указатель в начало буфера для чтения
    return output_buffer.getvalue()

# --- Функции для проверки подписки ---

async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Проверяет, подписан ли пользователь на канал.
    """
    try:
        chat_member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        # Статусы, которые считаются "подписанным": member, administrator, creator
        if chat_member.status in ['member', 'administrator', 'creator']:
            return True
        else:
            return False
    except Exception as e:
        logger.error(f"Ошибка при проверке подписки для пользователя {user_id}: {e}")
        return False

# --- Обработчики команд и сообщений ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение и кнопку 'Начать использовать'."""
    keyboard = [
        [InlineKeyboardButton("Начать использовать", callback_data="start_bot")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Привет! Я бот для удаления водяных знаков из JSON-файлов.\n"
        "Чтобы начать, пожалуйста, нажмите кнопку ниже.",
        reply_markup=reply_markup
    )

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатия на Inline-кнопки."""
    query = update.callback_query
    await query.answer() # Убирает "часики" с кнопки

    user_id = query.from_user.id
    if query.data == "start_bot":
        is_subscribed = await check_subscription(user_id, context)
        if is_subscribed:
            await query.message.reply_text("Отлично! Теперь отправьте мне JSON-файл.")
        else:
            keyboard = [
                [InlineKeyboardButton("Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
                [InlineKeyboardButton("Проверить подписку", callback_data="check_subscription")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text(
                "Пожалуйста, подпишитесь на канал, чтобы использовать бота:",
                reply_markup=reply_markup
            )
    elif query.data == "check_subscription":
        is_subscribed = await check_subscription(user_id, context)
        if is_subscribed:
            await query.message.reply_text("Спасибо за подписку! Теперь отправьте мне JSON-файл.")
        else:
            await query.message.reply_text("Вы все еще не подписаны. Пожалуйста, подпишитесь и попробуйте снова.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает полученные документы (JSON-файлы)."""
    user_id = update.message.from_user.id
    is_subscribed = await check_subscription(user_id, context)

    if not is_subscribed:
        keyboard = [
            [InlineKeyboardButton("Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton("Проверить подписку", callback_data="check_subscription")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Пожалуйста, подпишитесь на канал, чтобы использовать бота:",
            reply_markup=reply_markup
        )
        return

    document = update.message.document
    if document.mime_type != 'application/json':
        await update.message.reply_text("Пожалуйста, отправьте JSON-файл.")
        return

    try:
        file_bytes = await context.bot.get_file(document.file_id)
        json_bytes = await file_bytes.download_as_bytearray()
        json_bytes = bytes(json_bytes)  # Convert bytearray to bytes
        processed_data, watermark_removed = process_json_data(json_bytes)
        processed_json_bytes = write_json_data(processed_data)

        # Отправляем обработанный файл обратно пользователю
        filename = document.file_name if document.file_name else "processed.json"
        await update.message.reply_document(
            document=InputFile(processed_json_bytes, filename=filename),
            caption="Обработанный JSON-файл (водяные знаки удалены)." if watermark_removed else "Обработанный JSON-файл (водяные знаки не найдены)."
        )

    except ValueError as e:
        await update.message.reply_text(str(e))
    except Exception as e:
        logger.error(f"Ошибка при обработке файла: {e}")
        await update.message.reply_text("Произошла ошибка при обработке файла. Пожалуйста, попробуйте позже.")


# --- Запуск бота ---

def main() -> None:
    """Запускает бота."""
    application = Application.builder().token(BOT_TOKEN).build()

    # Обработчики команд
    application.add_handler(CommandHandler("start", start_command))

    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.Document.MimeType("application/json"), handle_document))

    # Обработчики CallbackQuery (для кнопок)
    application.add_handler(CallbackQueryHandler(handle_callback_query))


    # Запуск бота
    application.run_polling()

if __name__ == "__main__":
    main()
