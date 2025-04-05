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

    c.execute('''CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, 
        code TEXT UNIQUE NOT NULL, is_active BOOLEAN DEFAULT 1)''')

    c.execute('''CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY, name TEXT NOT NULL,
        category_id INTEGER, price REAL DEFAULT 0,
        is_active BOOLEAN DEFAULT 1,
        FOREIGN KEY (category_id) REFERENCES categories (id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, telegram_id INTEGER UNIQUE NOT NULL,
        balance REAL DEFAULT 0, is_active BOOLEAN DEFAULT 1)''')

    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY, user_id INTEGER,
        product_id INTEGER, amount REAL,
        customer_info TEXT, status TEXT DEFAULT 'pending',
        rejection_note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        note TEXT,
        FOREIGN KEY (user_id) REFERENCES users (telegram_id),
        FOREIGN KEY (product_id) REFERENCES products (id))''')

    default_categories = [
        ('إنترنت', 'internet'),
        ('جوال', 'mobile'),
        ('خط أرضي', 'landline')
    ]

    for name, code in default_categories:
        c.execute('INSERT OR IGNORE INTO categories (name, code) VALUES (?, ?)', 
                 (name, code))

    conn.commit()
    conn.close()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    c.execute('INSERT OR IGNORE INTO users (telegram_id, balance) VALUES (?, ?)', 
              (user_id, 0))
    conn.commit()

    welcome_text = f"مرحباً بك في متجرنا!\nمعرف التيليجرام: {user_id}"
    await update.message.reply_text(welcome_text)

    await show_main_menu(update, context)

    conn.close()

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    c.execute('SELECT name, code FROM categories WHERE is_active = 1')
    categories = c.fetchall()

    keyboard = []
    for category in categories:
        keyboard.append([InlineKeyboardButton(category[0], 
                                            callback_data=f'cat_{category[1]}')])

    keyboard.append([
        InlineKeyboardButton("رصيدي", callback_data='balance'),
        InlineKeyboardButton("طلباتي", callback_data='my_orders')
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.message.edit_text(
            'اختر القسم:', reply_markup=reply_markup)
    else:
        await update.message.reply_text(
            'اختر القسم:', reply_markup=reply_markup)

    conn.close()

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'back':
        await show_main_menu(update, context)
        return

    if query.data == 'balance':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', 
                 (update.effective_user.id,))
        balance = c.fetchone()[0]
        conn.close()

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            f"رصيدك الحالي: {balance} ليرة سوري",
            reply_markup=reply_markup)
        return

    if query.data == 'my_orders':
        await show_orders(update, context)
        return

    if query.data.startswith('cat_'):
        await show_category_products(update, context, query.data[4:])
        return

    if query.data.startswith('buy_'):
        product_id = int(query.data[4:])
        context.user_data['product_id'] = product_id
        await query.message.edit_text("الرجاء إدخال بيانات الزبون:")
        return "WAITING_CUSTOMER_INFO"

async def show_category_products(update: Update, context: ContextTypes.DEFAULT_TYPE, category_code: str):
    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    c.execute('''SELECT p.id, p.name FROM products p 
                 JOIN categories c ON p.category_id = c.id 
                 WHERE c.code = ? AND p.is_active = 1''', (category_code,))
    products = c.fetchall()

    keyboard = []
    for product in products:
        keyboard.append([InlineKeyboardButton(product[1], 
                                            callback_data=f'buy_{product[0]}')])
    keyboard.append([InlineKeyboardButton("رجوع", callback_data='back')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(
        "اختر الشركة:", reply_markup=reply_markup)

    conn.close()

async def handle_customer_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['customer_info'] = update.message.text
    await update.message.reply_text("الرجاء إدخال المبلغ:")
    return "WAITING_AMOUNT"

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        if amount <= 0:
            await update.message.reply_text("الرجاء إدخال مبلغ صحيح أكبر من الصفر")
            return ConversationHandler.END

        context.user_data['amount'] = amount

        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', 
                 (update.effective_user.id,))
        balance = c.fetchone()[0]
        conn.close()

        if amount > balance:
            await update.message.reply_text(
                f"عذراً، رصيدك غير كافي. رصيدك الحالي: {balance} ليرة سوري")
            return ConversationHandler.END

        keyboard = [
            [
                InlineKeyboardButton("تأكيد", callback_data='confirm_purchase'),
                InlineKeyboardButton("إلغاء", callback_data='cancel_purchase')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"سيتم خصم {amount} ليرة سوري من رصيدك.\n"
            f"اضغط على تأكيد لإتمام العملية.",
            reply_markup=reply_markup)
        return "WAITING_CONFIRMATION"

    except ValueError:
        await update.message.reply_text("الرجاء إدخال مبلغ صحيح")
        return ConversationHandler.END

async def handle_purchase_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'cancel_purchase':
        await query.message.edit_text("تم إلغاء العملية.")
        return ConversationHandler.END

    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    try:
        c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?',
                  (context.user_data['amount'], update.effective_user.id))

        c.execute('''INSERT INTO orders 
                     (user_id, product_id, amount, customer_info) 
                     VALUES (?, ?, ?, ?)''',
                  (update.effective_user.id, context.user_data['product_id'],
                   context.user_data['amount'], context.user_data['customer_info']))

        conn.commit()

        await query.message.edit_text("تم تسجيل طلبك بنجاح!")

    except Exception as e:
        conn.rollback()
        await query.message.edit_text("حدث خطأ أثناء تسجيل الطلب.")
        print(f"Error: {e}")

    finally:
        conn.close()

    return ConversationHandler.END

async def show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info, 
                        o.created_at, o.note, o.rejection_note
                 FROM orders o 
                 JOIN products p ON o.product_id = p.id 
                 WHERE o.user_id = ? 
                 ORDER BY o.created_at DESC''', (update.effective_user.id,))
    orders = c.fetchall()
    conn.close()

    if not orders:
        keyboard = [[InlineKeyboardButton("رجوع", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.callback_query.message.edit_text(
            "لا يوجد لديك طلبات.",
            reply_markup=reply_markup)
        return

    for order in orders:
        status_text = {
            'pending': 'قيد المعالجة',
            'accepted': 'مقبول',
            'rejected': 'مرفوض'
        }.get(order[3], order[3])

        message = (
            f"رقم الطلب: {order[0]}\n"
            f"الشركة: {order[1]}\n"
            f"المبلغ: {order[2]} ليرة سوري\n"
            f"الحالة: {status_text}\n"
            f"بيانات الزبون: {order[4]}\n"
            f"التاريخ: {order[5]}"
        )

        if order[3] == 'rejected' and order[7]:
            message += f"\nسبب الرفض: {order[7]}"
        if order[6]:
            message += f"\nملاحظة: {order[6]}"

        keyboard = []
        if order[3] == 'pending':
            keyboard.append([InlineKeyboardButton(
                "إلغاء الطلب",
                callback_data=f'cancel_order_{order[0]}')])

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.callback_query.message.reply_text(
            message,
            reply_markup=reply_markup)


def run_flask():
    app.run(host='0.0.0.0', port=5000)

def run_bot():
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        print("Error: Bot token not found")
        return

    application = Application.builder().token(bot_token).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_callback)],
        states={
            "WAITING_CUSTOMER_INFO": [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    handle_customer_info)
            ],
            "WAITING_AMOUNT": [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    handle_amount)
            ],
            "WAITING_CONFIRMATION": [
                CallbackQueryHandler(handle_purchase_confirmation)
            ]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)]
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(conv_handler)

    print("Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    init_db()
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    run_bot()