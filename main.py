import os
import telegram
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)
from flask import Flask, render_template, request, redirect, url_for, session
import sqlite3
import shutil
import requests
from threading import Thread
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Initialize Flask app
app = Flask(__name__)

def init_db():
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    # Setting timezone for database
    c.execute("PRAGMA timezone = '+03:00'")

    # Create tables if they don't exist
    c.execute('''CREATE TABLE IF NOT EXISTS products 
                 (id INTEGER PRIMARY KEY, name TEXT, category TEXT, is_active BOOLEAN DEFAULT 1,
                  enable_speeds BOOLEAN DEFAULT 0,
                  enable_packages BOOLEAN DEFAULT 0,
                  enable_custom_amount BOOLEAN DEFAULT 1)''')

    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY, telegram_id INTEGER, balance REAL, 
                  phone_number TEXT, is_active BOOLEAN DEFAULT 1, note TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product_id INTEGER, amount REAL, 
                  customer_info TEXT, status TEXT DEFAULT 'pending', rejection_note TEXT,
                  created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours')), note TEXT)''')

    c.execute('''CREATE TABLE IF NOT EXISTS categories
                 (id INTEGER PRIMARY KEY, name TEXT, identifier TEXT, is_active BOOLEAN DEFAULT 1)''')

    conn.commit()
    conn.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    # Add user if not exists
    c.execute('SELECT * FROM users WHERE telegram_id = ?', (user_id,))
    if not c.fetchone():
        c.execute('INSERT INTO users (telegram_id, balance) VALUES (?, ?)', (user_id, 0))
        conn.commit()

    # Get active categories
    c.execute('SELECT name, identifier FROM categories WHERE is_active = 1')
    categories = c.fetchall()
    conn.close()

    keyboard = []
    row = []
    for i, category in enumerate(categories):
        row.append(InlineKeyboardButton(category[0], callback_data=f'cat_{category[1]}'))
        if len(row) == 3 or i == len(categories) - 1:
            keyboard.append(row)
            row = []

    keyboard.append([
        InlineKeyboardButton("رصيدي", callback_data='balance'),
        InlineKeyboardButton("طلباتي", callback_data='my_orders')
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_message = f"""مرحبا بك في نظام تسديد الفواتير
معرف التيليجرام الخاص بك هو: {user_id}
يمكنك استخدام هذا المعرف للتواصل مع الإدارة."""

    await update.message.reply_text(welcome_message)
    await update.message.reply_text(
        'اهلا بك في تسديد الفواتير الرجاء الاختيار علما ان مدة التسديد تتراوح بين 10 والساعتين عدا العطل والضغط يوجد تاخير والدوام من 9ص حتى 9 م',
        reply_markup=reply_markup
    )

def run_flask():
    app.run(host='0.0.0.0', port=5000)

def run_bot():
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        print("Error: Bot token not found")
        return

    application = Application.builder().token(bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.run_polling()

if __name__ == '__main__':
    init_db()
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    run_bot()