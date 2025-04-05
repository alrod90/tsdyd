
import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from threading import Thread
import requests

app = Flask(__name__)

def init_db():
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    
    # حذف الجداول القديمة
    c.execute('DROP TABLE IF EXISTS orders')
    c.execute('DROP TABLE IF EXISTS products')
    c.execute('DROP TABLE IF EXISTS categories')
    c.execute('DROP TABLE IF EXISTS users')

    # إنشاء الجداول من جديد
    c.execute('''CREATE TABLE categories (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        code TEXT UNIQUE NOT NULL,
        is_active BOOLEAN DEFAULT 1
    )''')

    c.execute('''CREATE TABLE products (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        category_id INTEGER,
        is_active BOOLEAN DEFAULT 1,
        FOREIGN KEY (category_id) REFERENCES categories (id)
    )''')

    c.execute('''CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        telegram_id INTEGER UNIQUE NOT NULL,
        balance REAL DEFAULT 0,
        is_active BOOLEAN DEFAULT 1
    )''')

    c.execute('''CREATE TABLE orders (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        product_id INTEGER,
        amount REAL,
        customer_info TEXT,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        note TEXT,
        FOREIGN KEY (user_id) REFERENCES users (telegram_id),
        FOREIGN KEY (product_id) REFERENCES products (id)
    )''')

    # إضافة الأقسام الافتراضية
    categories = [
        ('إنترنت', 'internet'),
        ('جوال', 'mobile'),
        ('خط أرضي', 'landline')
    ]
    
    for name, code in categories:
        c.execute('INSERT INTO categories (name, code) VALUES (?, ?)', (name, code))
    
    # إضافة بعض المنتجات للتجربة
    products = [
        ('باقة 100 ميجا', 50000, 1),
        ('باقة 200 ميجا', 90000, 1),
        ('رصيد 1000', 1000, 2),
        ('رصيد 5000', 5000, 2),
        ('خط منزلي', 15000, 3)
    ]
    
    for name, price, category_id in products:
        c.execute('INSERT INTO products (name, price, category_id) VALUES (?, ?, ?)',
                 (name, price, category_id))

    conn.commit()
    conn.close()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (telegram_id, balance) VALUES (?, ?)', 
              (user_id, 0))
    conn.commit()
    conn.close()

    keyboard = [
        [InlineKeyboardButton("إنترنت", callback_data='cat_internet')],
        [InlineKeyboardButton("جوال", callback_data='cat_mobile')],
        [InlineKeyboardButton("خط أرضي", callback_data='cat_landline')],
        [InlineKeyboardButton("رصيدي", callback_data='balance'),
         InlineKeyboardButton("طلباتي", callback_data='my_orders')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(f"مرحباً بك في متجرنا!\nمعرف التيليجرام: {user_id}")
    await update.message.reply_text("اختر القسم:", reply_markup=reply_markup)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'back':
        keyboard = [
            [InlineKeyboardButton("إنترنت", callback_data='cat_internet')],
            [InlineKeyboardButton("جوال", callback_data='cat_mobile')],
            [InlineKeyboardButton("خط أرضي", callback_data='cat_landline')],
            [InlineKeyboardButton("رصيدي", callback_data='balance'),
             InlineKeyboardButton("طلباتي", callback_data='my_orders')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر القسم:", reply_markup=reply_markup)
        return

    elif query.data == 'balance':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', 
                 (update.effective_user.id,))
        balance = c.fetchone()[0]
        conn.close()

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            f"رصيدك الحالي: {balance} ليرة سورية",
            reply_markup=reply_markup)
        return

    elif query.data == 'my_orders':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('''
            SELECT o.id, p.name, o.amount, o.status, o.customer_info
            FROM orders o
            JOIN products p ON o.product_id = p.id
            WHERE o.user_id = ?
            ORDER BY o.created_at DESC LIMIT 5
        ''', (update.effective_user.id,))
        orders = c.fetchall()
        conn.close()

        if not orders:
            keyboard = [[InlineKeyboardButton("رجوع", callback_data='back')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(
                "لا يوجد لديك طلبات سابقة",
                reply_markup=reply_markup)
            return

        message = "طلباتك الأخيرة:\n\n"
        for order in orders:
            status_text = "قيد المعالجة" if order[3] == 'pending' else order[3]
            message += f"رقم الطلب: {order[0]}\n"
            message += f"المنتج: {order[1]}\n"
            message += f"المبلغ: {order[2]}\n"
            message += f"الحالة: {status_text}\n"
            message += "---------------\n"

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup)
        return

    elif query.data.startswith('cat_'):
        category_code = query.data[4:]
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        
        c.execute('''
            SELECT p.id, p.name, p.price
            FROM products p
            JOIN categories c ON p.category_id = c.id
            WHERE c.code = ? AND p.is_active = 1
        ''', (category_code,))
        products = c.fetchall()
        conn.close()

        keyboard = []
        for product in products:
            keyboard.append([InlineKeyboardButton(
                f"{product[1]} - {product[2]} ل.س",
                callback_data=f'buy_{product[0]}')]
            )
        keyboard.append([InlineKeyboardButton("رجوع", callback_data='back')])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر المنتج:", reply_markup=reply_markup)
        return

def run_flask():
    app.run(host='0.0.0.0', port=5000)

def run_bot():
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        print("Error: Bot token not found")
        return

    application = Application.builder().token(bot_token).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(button_click))
    
    print("Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    init_db()
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    run_bot()
