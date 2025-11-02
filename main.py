import logging
import sqlite3
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, JobQueue

# ===== КОНФИГУРАЦИЯ =====
BOT_TOKEN = "8295362408:AAErt08BMNG2ZJ5b0hqrHIuDA26uNWfzH1A"
ADMIN_IDS = [8327465722]  # Ваш ID
GROUP_ID = -1003247909803  # ID группы для уведомлений
DATABASE_NAME = "numbers_bot.db"

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ===== БАЗА ДАННЫХ =====
class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DATABASE_NAME, check_same_thread=False)
        self.create_tables()
    
    def create_tables(self):
        cursor = self.conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0,
                registered_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Таблица номеров
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                phone_number TEXT,
                status TEXT DEFAULT 'pending',
                queue_status TEXT DEFAULT 'free', -- free, busy, completed
                submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                activated_at DATETIME,
                last_code_request DATETIME,
                current_service TEXT, -- для какой услуги используется
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        # Таблица кодов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number_id INTEGER,
                code TEXT,
                service TEXT,
                submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                FOREIGN KEY (number_id) REFERENCES numbers (id)
            )
        ''')
        
        # Таблица очереди
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number_id INTEGER,
                service TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                status TEXT DEFAULT 'active',
                FOREIGN KEY (number_id) REFERENCES numbers (id)
            )
        ''')
        
        self.conn.commit()
    
    def add_user(self, user_id, username, first_name):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name) 
            VALUES (?, ?, ?)
        ''', (user_id, username, first_name))
        self.conn.commit()
    
    def add_number(self, user_id, phone_number):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO numbers (user_id, phone_number) 
            VALUES (?, ?)
        ''', (user_id, phone_number))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_user_numbers(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT n.*, u.username, u.first_name 
            FROM numbers n 
            JOIN users u ON n.user_id = u.user_id 
            WHERE n.user_id = ? 
            ORDER BY n.submitted_at DESC
        ''', (user_id,))
        return cursor.fetchall()
    
    def get_pending_numbers(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT n.*, u.username, u.first_name 
            FROM numbers n 
            JOIN users u ON n.user_id = u.user_id 
            WHERE n.status = 'pending'
            ORDER BY n.submitted_at DESC
        ''')
        return cursor.fetchall()
    
    def get_active_numbers(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT n.*, u.username, u.first_name 
            FROM numbers n 
            JOIN users u ON n.user_id = u.user_id 
            WHERE n.status = 'active'
            ORDER BY n.submitted_at DESC
        ''')
        return cursor.fetchall()
    
    def get_free_numbers(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT n.*, u.username, u.first_name 
            FROM numbers n 
            JOIN users u ON n.user_id = u.user_id 
            WHERE n.status = 'active' AND n.queue_status = 'free'
            ORDER BY n.submitted_at DESC
        ''')
        return cursor.fetchall()
    
    def get_busy_numbers(self):
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT n.*, u.username, u.first_name, q.service, q.started_at
            FROM numbers n 
            JOIN users u ON n.user_id = u.user_id 
            JOIN queue q ON n.id = q.number_id
            WHERE n.queue_status = 'busy' AND q.status = 'active'
            ORDER BY q.started_at ASC
        ''')
        return cursor.fetchall()
    
    def get_all_numbers_with_users(self):
        """Получить все номера с информацией о пользователях"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT 
                n.id,
                n.phone_number,
                n.status,
                n.queue_status,
                n.current_service,
                n.submitted_at,
                u.user_id,
                u.username,
                u.first_name
            FROM numbers n
            JOIN users u ON n.user_id = u.user_id
            WHERE n.status IN ('active', 'pending')
            ORDER BY 
                CASE 
                    WHEN n.queue_status = 'busy' THEN 1
                    WHEN n.queue_status = 'free' THEN 2
                    ELSE 3
                END,
                n.submitted_at DESC
        ''')
        return cursor.fetchall()
    
    def update_number_status(self, number_id, status):
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE numbers SET status = ?, activated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        ''', (status, number_id))
        self.conn.commit()
    
    def update_queue_status(self, number_id, queue_status, service=None):
        cursor = self.conn.cursor()
        
        if queue_status == 'busy':
            # Добавляем в очередь
            cursor.execute('''
                INSERT INTO queue (number_id, service) VALUES (?, ?)
            ''', (number_id, service))
            cursor.execute('''
                UPDATE numbers SET queue_status = ?, current_service = ? WHERE id = ?
            ''', (queue_status, service, number_id))
        elif queue_status == 'free':
            # Завершаем активную задачу в очереди
            cursor.execute('''
                UPDATE queue SET status = 'completed', completed_at = CURRENT_TIMESTAMP 
                WHERE number_id = ? AND status = 'active'
            ''', (number_id,))
            cursor.execute('''
                UPDATE numbers SET queue_status = ?, current_service = NULL WHERE id = ?
            ''', (queue_status, number_id))
        elif queue_status == 'completed':
            cursor.execute('''
                UPDATE numbers SET queue_status = ? WHERE id = ?
            ''', (queue_status, number_id))
        
        self.conn.commit()

db = Database()

# ===== КЛАВИАТУРЫ =====
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["📱 Сдать номер", "📊 Мои номера"],
        ["📋 Очередь номеров", "ℹ️ Помощь"],
        ["👑 Админ", "💬 Поддержка"]
    ], resize_keyboard=True)

def get_admin_number_keyboard(number_id, user_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔐 Запросить код", callback_data=f"req_code_{number_id}"),
            InlineKeyboardButton("✅ Принять", callback_data=f"accept_{number_id}")
        ],
        [
            InlineKeyboardButton("🔄 Взять в работу", callback_data=f"take_{number_id}"),
            InlineKeyboardButton("✅ Завершить", callback_data=f"complete_{number_id}")
        ],
        [
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{number_id}"),
            InlineKeyboardButton("🚫 Заблокировать", callback_data=f"block_{number_id}")
        ],
        [
            InlineKeyboardButton("💬 Написать", callback_data=f"msg_{user_id}"),
            InlineKeyboardButton("👤 Инфо", callback_data=f"info_{user_id}")
        ]
    ])

def get_service_keyboard(number_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📱 WhatsApp", callback_data=f"service_whatsapp_{number_id}"),
            InlineKeyboardButton("✈️ Telegram", callback_data=f"service_telegram_{number_id}")
        ],
        [
            InlineKeyboardButton("📧 Gmail", callback_data=f"service_gmail_{number_id}"),
            InlineKeyboardButton("🛍️ Avito", callback_data=f"service_avito_{number_id}")
        ],
        [
            InlineKeyboardButton("🚗 Юла", callback_data=f"service_yula_{number_id}"),
            InlineKeyboardButton("🔐 Другое", callback_data=f"service_other_{number_id}")
        ]
    ])

def get_admin_main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Ожидают", callback_data="admin_pending"),
            InlineKeyboardButton("🟢 Активные", callback_data="admin_active")
        ],
        [
            InlineKeyboardButton("🆓 Свободные", callback_data="admin_free"),
            InlineKeyboardButton("🔴 Занятые", callback_data="admin_busy")
        ],
        [
            InlineKeyboardButton("👥 Все номера", callback_data="admin_all"),
            InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")
        ]
    ])

# ===== КОМАНДА ОЧЕРЕДИ С ИНФОРМАЦИЕЙ ОТПРАВИТЕЛЯ =====
async def show_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    # Проверяем права администратора
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Эта команда только для администраторов")
        return
    
    # Получаем данные об очереди
    free_numbers = db.get_free_numbers()
    busy_numbers = db.get_busy_numbers()
    
    queue_text = "📋 <b>ОЧЕРЕДЬ НОМЕРОВ</b>\n\n"
    
    # Занятые номера
    queue_text += "🔴 <b>ЗАНЯТЫЕ НОМЕРА:</b>\n"
    if busy_numbers:
        for num in busy_numbers:
            started_time = datetime.strptime(num[9], '%Y-%m-%d %H:%M:%S') if num[9] else datetime.now()
            duration = datetime.now() - started_time
            minutes = int(duration.total_seconds() / 60)
            
            user_info = f"👤 @{num[7]}" if num[7] and num[7] != 'N/A' else f"👤 {num[8]}"
            queue_text += f"• <code>{num[2]}</code>\n  └─ {user_info} | {num[8]} | {minutes} мин\n"
    else:
        queue_text += "• Нет занятых номеров\n"
    
    queue_text += "\n🆓 <b>СВОБОДНЫЕ НОМЕРА:</b>\n"
    if free_numbers:
        for num in free_numbers:
            user_info = f"👤 @{num[7]}" if num[7] and num[7] != 'N/A' else f"👤 {num[8]}"
            submitted_time = datetime.strptime(num[5], '%Y-%m-%d %H:%M:%S') if num[5] else datetime.now()
            time_ago = datetime.now() - submitted_time
            hours_ago = int(time_ago.total_seconds() / 3600)
            
            queue_text += f"• <code>{num[2]}</code>\n  └─ {user_info} | {hours_ago} ч назад\n"
    else:
        queue_text += "• Нет свободных номеров\n"
    
    queue_text += f"\n📊 <b>ИТОГО:</b> 🔴 {len(busy_numbers)} | 🆓 {len(free_numbers)}"
    
    await update.message.reply_text(queue_text, parse_mode='HTML')

async def show_detailed_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подробная очередь со всей информацией"""
    user_id = update.message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Эта команда только для администраторов")
        return
    
    # Получаем все номера с информацией о пользователях
    all_numbers = db.get_all_numbers_with_users()
    
    if not all_numbers:
        await update.message.reply_text("📭 <b>Нет номеров в системе</b>", parse_mode='HTML')
        return
    
    queue_text = "👥 <b>ПОЛНАЯ ОЧЕРЕДЬ НОМЕРОВ</b>\n\n"
    
    # Группируем по статусу
    busy_numbers = [n for n in all_numbers if n[3] == 'busy']
    free_numbers = [n for n in all_numbers if n[3] == 'free' and n[2] == 'active']
    pending_numbers = [n for n in all_numbers if n[2] == 'pending']
    
    # Занятые номера
    queue_text += "🔴 <b>ЗАНЯТЫЕ НОМЕРА:</b>\n"
    if busy_numbers:
        for num in busy_numbers:
            user_info = f"👤 @{num[7]}" if num[7] and num[7] != 'N/A' else f"👤 {num[8]} (ID: {num[6]})"
            queue_text += f"• <code>{num[1]}</code> - {num[4]}\n  └─ {user_info}\n"
    else:
        queue_text += "• Нет занятых номеров\n"
    
    # Свободные номера
    queue_text += "\n🆓 <b>СВОБОДНЫЕ НОМЕРА:</b>\n"
    if free_numbers:
        for num in free_numbers:
            user_info = f"👤 @{num[7]}" if num[7] and num[7] != 'N/A' else f"👤 {num[8]} (ID: {num[6]})"
            queue_text += f"• <code>{num[1]}</code>\n  └─ {user_info}\n"
    else:
        queue_text += "• Нет свободных номеров\n"
    
    # Ожидающие номера
    queue_text += "\n⏳ <b>ОЖИДАЮЩИЕ ПРОВЕРКИ:</b>\n"
    if pending_numbers:
        for num in pending_numbers:
            user_info = f"👤 @{num[7]}" if num[7] and num[7] != 'N/A' else f"👤 {num[8]} (ID: {num[6]})"
            submitted_time = datetime.strptime(num[5], '%Y-%m-%d %H:%M:%S') if num[5] else datetime.now()
            time_ago = datetime.now() - submitted_time
            minutes_ago = int(time_ago.total_seconds() / 60)
            
            queue_text += f"• <code>{num[1]}</code>\n  └─ {user_info} | {minutes_ago} мин назад\n"
    else:
        queue_text += "• Нет ожидающих номеров\n"
    
    # Статистика
    queue_text += f"\n📊 <b>СТАТИСТИКА:</b>\n"
    queue_text += f"🔴 Занято: <b>{len(busy_numbers)}</b>\n"
    queue_text += f"🆓 Свободно: <b>{len(free_numbers)}</b>\n"
    queue_text += f"⏳ Ожидают: <b>{len(pending_numbers)}</b>\n"
    queue_text += f"📱 Всего: <b>{len(all_numbers)}</b>"
    
    await update.message.reply_text(queue_text, parse_mode='HTML')

# ===== ОСНОВНЫЕ КОМАНДЫ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    db.add_user(user.id, user.username, user.first_name)
    
    welcome_text = (
        "🔥 <b>MAX БОТ ДЛЯ СДАЧИ НОМЕРОВ</b>\n\n"
        "📱 <b>Зарабатывайте на своих номерах</b>\n"
        "⚡ <b>Система очереди номеров</b>\n"
        "🔒 <b>Анонимность и безопасность</b>\n\n"
        "<b>Выберите действие:</b>"
    )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )

async def handle_number_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    phone_number = update.message.text.strip()
    
    # Валидация номера
    if not any(c.isdigit() for c in phone_number) or len(phone_number) < 5:
        await update.message.reply_text(
            "❌ <b>Неверный формат номера!</b>\n\n"
            "Отправьте номер в любом формате:\n"
            "• +79123456789\n"
            "• 89123456789\n"
            "• 9123456789\n\n"
            "<b>Попробуйте еще раз:</b>",
            parse_mode='HTML'
        )
        return
    
    # Добавляем номер в базу
    number_id = db.add_number(user_id, phone_number)
    
    # Уведомление пользователю
    await update.message.reply_text(
        f"✅ <b>НОМЕР ПРИНЯТ!</b>\n\n"
        f"📱 <b>Ваш номер:</b> <code>{phone_number}</code>\n"
        f"⏳ <b>Статус:</b> Ожидает проверки\n"
        f"📋 <b>Очередь:</b> Свободен\n\n"
        f"Как только заказчик проверит номер, вы получите уведомление.",
        reply_markup=get_main_keyboard(),
        parse_mode='HTML'
    )
    
    # Уведомление администраторам
    await notify_admins(context, number_id, user_id, phone_number, update.message.from_user)

async def my_numbers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    numbers = db.get_user_numbers(user_id)
    
    if not numbers:
        await update.message.reply_text(
            "📭 <b>У вас нет активных номеров</b>\n\n"
            "Нажмите <b>Сдать номер</b> чтобы добавить первый номер!",
            parse_mode='HTML'
        )
        return
    
    text = "📊 <b>ВАШИ НОМЕРА</b>\n\n"
    for num in numbers:
        status_icons = {
            'pending': '⏳',
            'active': '🟢', 
            'rejected': '❌',
            'blocked': '🚫'
        }
        queue_icons = {
            'free': '🆓',
            'busy': '🔴',
            'completed': '✅'
        }
        icon = status_icons.get(num[3], '❓')
        queue_icon = queue_icons.get(num[4], '❓')
        text += f"{icon}{queue_icon} <code>{num[2]}</code> - {num[3]} | {num[4]}\n"
    
    await update.message.reply_text(text, parse_mode='HTML')

# ===== АДМИН СИСТЕМА =====
async def notify_admins(context, number_id, user_id, phone_number, user):
    admin_message = (
        f"🆕 <b>НОВЫЙ НОМЕР #{number_id}</b>\n\n"
        f"📱 <b>Номер:</b> <code>{phone_number}</code>\n"
        f"👤 <b>Отправитель:</b> @{user.username or 'N/A'}\n"
        f"📛 <b>Имя:</b> {user.first_name or 'N/A'}\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"📋 <b>Очередь:</b> Свободен\n"
        f"🕒 <b>Время:</b> {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}"
    )
    
    # Отправка всем админам
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_message,
                reply_markup=get_admin_number_keyboard(number_id, user_id),
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Ошибка отправки админу {admin_id}: {e}")
    
    # Отправка в группу
    try:
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=admin_message,
            reply_markup=get_admin_number_keyboard(number_id, user_id),
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Ошибка отправки в группу: {e}")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Доступ запрещен")
        return
    
    admin_text = (
        "👑 <b>ПАНЕЛЬ АДМИНИСТРАТОРА MAX BOT</b>\n\n"
        "<b>Выберите действие:</b>"
    )
    
    await update.message.reply_text(
        admin_text,
        reply_markup=get_admin_main_keyboard(),
        parse_mode='HTML'
    )

async def admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("req_code_"):
        number_id = int(data.split("_")[2])
        numbers = db.get_pending_numbers() + db.get_active_numbers()
        number_data = next((n for n in numbers if n[0] == number_id), None)
        
        if number_data:
            db.update_number_status(number_id, "active")
            
            # Отправка запроса кода пользователю
            await context.bot.send_message(
                chat_id=number_data[1],
                text="🔐 <b>ЗАПРОС КОДА ПОДТВЕРЖДЕНИЯ</b>\n\n"
                     "Заказчик запросил код с вашего номера.\n\n"
                     "📲 <b>Отправьте код который пришел на ваш номер:</b>",
                parse_mode='HTML'
            )
            
            # Обновление сообщения админу
            user_info = f"👤 @{number_data[7]}" if number_data[7] and number_data[7] != 'N/A' else f"👤 {number_data[8]}"
            await query.edit_message_text(
                f"✅ <b>Запрос кода отправлен</b>\n\n"
                f"📱 Номер: <code>{number_data[2]}</code>\n"
                f"{user_info}\n"
                f"📋 Очередь: Свободен\n\n"
                f"Ожидайте код...",
                parse_mode='HTML'
            )

    elif data.startswith("take_"):
        number_id = int(data.split("_")[1])
        numbers = db.get_pending_numbers() + db.get_active_numbers()
        number_data = next((n for n in numbers if n[0] == number_id), None)
        
        if number_data:
            user_info = f"👤 @{number_data[7]}" if number_data[7] and number_data[7] != 'N/A' else f"👤 {number_data[8]}"
            # Показываем выбор услуги
            await query.edit_message_text(
                f"🔄 <b>Взять номер в работу</b>\n\n"
                f"📱 Номер: <code>{number_data[2]}</code>\n"
                f"{user_info}\n\n"
                f"<b>Выберите услугу:</b>",
                reply_markup=get_service_keyboard(number_id),
                parse_mode='HTML'
            )

    elif data.startswith("service_"):
        parts = data.split("_")
        service_name = parts[1]
        number_id = int(parts[2])
        
        numbers = db.get_pending_numbers() + db.get_active_numbers()
        number_data = next((n for n in numbers if n[0] == number_id), None)
        
        if number_data:
            # Обновляем статус очереди
            db.update_queue_status(number_id, "busy", service_name)
            
            user_info = f"👤 @{number_data[7]}" if number_data[7] and number_data[7] != 'N/A' else f"👤 {number_data[8]}"
            
            # Уведомление пользователю
            await context.bot.send_message(
                chat_id=number_data[1],
                text=f"🔄 <b>НОМЕР ВЗЯТ В РАБОТУ</b>\n\n"
                     f"📱 Ваш номер <code>{number_data[2]}</code>\n"
                     f"📋 <b>Статус:</b> Занят ({service_name})\n\n"
                     f"Ожидайте запрос кода...",
                parse_mode='HTML'
            )
            
            # Обновление сообщения админу
            await query.edit_message_text(
                f"🔴 <b>НОМЕР ВЗЯТ В РАБОТУ</b>\n\n"
                f"📱 Номер: <code>{number_data[2]}</code>\n"
                f"{user_info}\n"
                f"📋 Услуга: {service_name}\n"
                f"🕒 Время: {datetime.now().strftime('%H:%M:%S')}\n\n"
                f"<b>Статус: ЗАНЯТ</b>",
                parse_mode='HTML'
            )

    elif data.startswith("complete_"):
        number_id = int(data.split("_")[1])
        numbers = db.get_pending_numbers() + db.get_active_numbers()
        number_data = next((n for n in numbers if n[0] == number_id), None)
        
        if number_data:
            # Освобождаем номер
            db.update_queue_status(number_id, "free")
            
            user_info = f"👤 @{number_data[7]}" if number_data[7] and number_data[7] != 'N/A' else f"👤 {number_data[8]}"
            
            # Уведомление пользователю
            await context.bot.send_message(
                chat_id=number_data[1],
                text=f"✅ <b>РАБОТА ЗАВЕРШЕНА</b>\n\n"
                     f"📱 Ваш номер <code>{number_data[2]}</code>\n"
                     f"📋 <b>Статус:</b> Свободен\n\n"
                     f"Номер снова доступен для использования!",
                parse_mode='HTML'
            )
            
            # Обновление сообщения админу
            await query.edit_message_text(
                f"🆓 <b>РАБОТА ЗАВЕРШЕНА</b>\n\n"
                f"📱 Номер: <code>{number_data[2]}</code>\n"
                f"{user_info}\n"
                f"📋 Статус: Свободен\n"
                f"🕒 Время: {datetime.now().strftime('%H:%M:%S')}",
                parse_mode='HTML'
            )

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "admin_pending":
        numbers = db.get_pending_numbers()
        if not numbers:
            await query.edit_message_text("📭 <b>Нет ожидающих номеров</b>", parse_mode='HTML')
            return
        
        text = "📋 <b>ОЖИДАЮЩИЕ НОМЕРА:</b>\n\n"
        for num in numbers:
            user_info = f"👤 @{num[7]}" if num[7] and num[7] != 'N/A' else f"👤 {num[8]} (ID: {num[1]})"
            text += f"🔸 <code>{num[2]}</code>\n  └─ {user_info}\n"
        
        await query.edit_message_text(text, parse_mode='HTML')
    
    elif query.data == "admin_active":
        numbers = db.get_active_numbers()
        if not numbers:
            await query.edit_message_text("🟢 <b>Нет активных номеров</b>", parse_mode='HTML')
            return
        
        text = "🟢 <b>АКТИВНЫЕ НОМЕРА:</b>\n\n"
        for num in numbers:
            user_info = f"👤 @{num[7]}" if num[7] and num[7] != 'N/A' else f"👤 {num[8]}"
            queue_status = "🆓 Свободен" if num[4] == 'free' else "🔴 Занят"
            text += f"🔹 <code>{num[2]}</code> - {queue_status}\n  └─ {user_info}\n"
        
        await query.edit_message_text(text, parse_mode='HTML')
    
    elif query.data == "admin_free":
        numbers = db.get_free_numbers()
        if not numbers:
            await query.edit_message_text("🆓 <b>Нет свободных номеров</b>", parse_mode='HTML')
            return
        
        text = "🆓 <b>СВОБОДНЫЕ НОМЕРА:</b>\n\n"
        for num in numbers:
            user_info = f"👤 @{num[7]}" if num[7] and num[7] != 'N/A' else f"👤 {num[8]} (ID: {num[1]})"
            submitted_time = datetime.strptime(num[5], '%Y-%m-%d %H:%M:%S') if num[5] else datetime.now()
            time_ago = datetime.now() - submitted_time
            hours_ago = int(time_ago.total_seconds() / 3600)
            
            text += f"🆓 <code>{num[2]}</code>\n  └─ {user_info} | {hours_ago} ч назад\n"
        
        await query.edit_message_text(text, parse_mode='HTML')
    
    elif query.data == "admin_busy":
        numbers = db.get_busy_numbers()
        if not numbers:
            await query.edit_message_text("🔴 <b>Нет занятых номеров</b>", parse_mode='HTML')
            return
        
        text = "🔴 <b>ЗАНЯТЫЕ НОМЕРА:</b>\n\n"
        for num in numbers:
            user_info = f"👤 @{num[7]}" if num[7] and num[7] != 'N/A' else f"👤 {num[8]} (ID: {num[1]})"
            started_time = datetime.strptime(num[9], '%Y-%m-%d %H:%M:%S')
            duration = datetime.now() - started_time
            minutes = int(duration.total_seconds() / 60)
            
            text += f"🔴 <code>{num[2]}</code> - {num[8]}\n  └─ {user_info} | {minutes} мин\n"
        
        await query.edit_message_text(text, parse_mode='HTML')
    
    elif query.data == "admin_all":
        await show_detailed_queue(update, context)
    
    elif query.data == "admin_stats":
        pending = len(db.get_pending_numbers())
        active = len(db.get_active_numbers())
        free = len(db.get_free_numbers())
        busy = len(db.get_busy_numbers())
        
        stats_text = (
            f"📊 <b>СТАТИСТИКА MAX BOT</b>\n\n"
            f"⏳ Ожидают: <b>{pending}</b>\n"
            f"🟢 Активные: <b>{active}</b>\n"
            f"🆓 Свободные: <b>{free}</b>\n"
            f"🔴 Занятые: <b>{busy}</b>\n"
            f"📱 Всего: <b>{pending + active}</b>"
        )
        
        await query.edit_message_text(stats_text, parse_mode='HTML')

# ===== ОБРАБОТЧИКИ СООБЩЕНИЙ =====
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "📱 Сдать номер":
        await update.message.reply_text(
            "📱 <b>Отправьте номер телефона:</b>\n\n"
            "Можно в любом формате:\n"
            "+79123456789\n"
            "89123456789\n"
            "9123456789",
            parse_mode='HTML'
        )
    elif text == "📊 Мои номера":
        await my_numbers(update, context)
    elif text == "📋 Очередь номеров":
        await show_queue(update, context)
    elif text == "👑 Админ":
        await admin_panel(update, context)
    elif text == "ℹ️ Помощь":
        await update.message.reply_text(
            "📖 <b>СИСТЕМА ОЧЕРЕДИ НОМЕРОВ</b>\n\n"
            "🆓 <b>Свободен</b> - номер доступен для работы\n"
            "🔴 <b>Занят</b> - номер используется для услуги\n"
            "✅ <b>Завершен</b> - работа с номером завершена\n\n"
            "Администраторы видят всю очередь и могут управлять статусами.",
            parse_mode='HTML'
        )
    elif text == "💬 Поддержка":
        await update.message.reply_text(
            "💬 <b>Поддержка</b>\n\n"
            "По всем вопросам обращайтесь к @admin",
            parse_mode='HTML'
        )
    else:
        await handle_number_submission(update, context)

# ===== ОБРАБОТКА КОДОВ =====
async def handle_code_from_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    code = update.message.text.strip()
    
    numbers = db.get_user_numbers(user_id)
    active_numbers = [n for n in numbers if n[3] == 'active']
    
    if not active_numbers:
        await update.message.reply_text("❌ У вас нет активных номеров для отправки кодов")
        return
    
    if len(code) < 3:
        await update.message.reply_text("❌ Код слишком короткий")
        return
    
    current_number = None
    for num in active_numbers:
        if num[4] == 'busy':
            current_number = num
            break
    
    if not current_number:
        await update.message.reply_text("❌ У вас нет номеров в работе")
        return
    
    user_info = f"👤 @{update.message.from_user.username}" if update.message.from_user.username else f"👤 {update.message.from_user.first_name}"
    
    # Отправляем код всем админам
    code_message = (
        f"📨 <b>ПОСТУПИЛ КОД!</b>\n\n"
        f"📱 Номер: <code>{current_number[2]}</code>\n"
        f"{user_info}\n"
        f"📋 Услуга: {current_number[5] or 'N/A'}\n"
        f"🔐 Код: <code>{code}</code>\n"
        f"🕒 Время: {datetime.now().strftime('%H:%M:%S')}"
    )
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=code_message,
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Ошибка отправки админу {admin_id}: {e}")
    
    try:
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=code_message,
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Ошибка отправки в группу: {e}")
    
    await update.message.reply_text(
        f"✅ <b>КОД УСПЕШНО ОТПРАВЛЕН!</b>\n\n"
        f"🔐 Код: <code>{code}</code>\n"
        f"📱 Номер: <code>{current_number[2]}</code>\n\n"
        f"Ожидайте следующих запросов.",
        parse_mode='HTML'
    )

# ===== ЗАПУСК БОТА =====
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("p", show_detailed_queue))  # Команда /p для подробной очереди
    application.add_handler(CommandHandler("queue", show_queue))
    application.add_handler(CommandHandler("admin", admin_panel))
    
    # Обработчики кнопок
    application.add_handler(CallbackQueryHandler(admin_actions, pattern="^(req_code|accept|reject|block|take|complete|service)_"))
    application.add_handler(CallbackQueryHandler(admin_menu, pattern="^admin_"))
    
    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    
    print("🔥 MAX Бот для номеров с очередью запущен!")
    application.run_polling()

if __name__ == "__main__":
    main()