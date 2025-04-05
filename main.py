
import os
import telegram
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters
)
from flask import Flask, render_template, request, redirect, url_for
import sqlite3
from threading import Thread

# Initialize Flask app
app = Flask(__name__)

# Database setup
def init_db():
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products 
                 (id INTEGER PRIMARY KEY, name TEXT, price REAL, category TEXT, is_active BOOLEAN DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY, telegram_id INTEGER, balance REAL)''')
    conn.commit()
    conn.close()

# Telegram bot commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # إضافة المستخدم إلى قاعدة البيانات إذا لم يكن موجوداً
    user_id = update.effective_user.id
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE telegram_id = ?', (user_id,))
    if not c.fetchone():
        c.execute('INSERT INTO users (telegram_id, balance) VALUES (?, ?)', (user_id, 0))
        conn.commit()
    conn.close()

    welcome_message = f"""مرحباً بك في متجرنا!
معرف التيليجرام الخاص بك هو: {user_id}
يمكنك استخدام هذا المعرف للتواصل مع الإدارة.
"""
    await update.message.reply_text(welcome_message)

    keyboard = [
        [InlineKeyboardButton("إنترنت", callback_data='cat_internet')],
        [InlineKeyboardButton("جوال", callback_data='cat_mobile')],
        [InlineKeyboardButton("خط أرضي", callback_data='cat_landline')],
        [InlineKeyboardButton("رصيدي", callback_data='balance')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('مرحباً بك في متجرنا! الرجاء اختيار القسم:', reply_markup=reply_markup)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('cat_'):
        category = query.data.split('_')[1]
        category_names = {
            'internet': 'إنترنت',
            'mobile': 'جوال',
            'landline': 'خط أرضي'
        }
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT * FROM products WHERE category = ? AND is_active = 1', (category,))
        products = c.fetchall()
        conn.close()
        
        if products:
            keyboard = []
            for product in products:
                keyboard.append([InlineKeyboardButton(f"{product[1]} - {product[2]} ريال", 
                                                    callback_data=f'buy_{product[0]}')])
            keyboard.append([InlineKeyboardButton("رجوع", callback_data='back')])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(
                f"المنتجات المتوفرة في قسم {category_names[category]}:",
                reply_markup=reply_markup
            )
        else:
            await query.message.edit_text(f"لا توجد منتجات متوفرة في قسم {category_names[category]}")
    
    elif query.data == 'balance':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', (update.effective_user.id,))
        result = c.fetchone()
        balance = result[0] if result else 0
        conn.close()
        await query.message.edit_text(f"رصيدك الحالي: {balance} ريال")
    
    elif query.data == 'back':
        keyboard = [
            [InlineKeyboardButton("إنترنت", callback_data='cat_internet')],
            [InlineKeyboardButton("جوال", callback_data='cat_mobile')],
            [InlineKeyboardButton("خط أرضي", callback_data='cat_landline')],
            [InlineKeyboardButton("رصيدي", callback_data='balance')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text('اختر القسم:', reply_markup=reply_markup)
    
    elif query.data.startswith('buy_'):
        product_id = int(query.data.split('_')[1])
        context.user_data['product_id'] = product_id
        await query.message.edit_text("الرجاء إدخال بيانات الزبون (الاسم، رقم الهاتف):")
        return "WAITING_CUSTOMER_INFO"

async def handle_customer_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    customer_info = update.message.text
    context.user_data['customer_info'] = customer_info
    
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT name, price FROM products WHERE id = ?', (context.user_data['product_id'],))
    product = c.fetchone()
    c.execute('SELECT balance FROM users WHERE telegram_id = ?', (update.effective_user.id,))
    user_balance = c.fetchone()[0]
    conn.close()
    
    if user_balance < product[1]:
        await update.message.reply_text("عذراً، رصيدك غير كافي لإتمام العملية.")
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"سيتم خصم {product[1]} ريال من رصيدك.\n"
        f"اضغط على تأكيد لإتمام العملية.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("تأكيد", callback_data='confirm_purchase'),
            InlineKeyboardButton("إلغاء", callback_data='cancel_purchase')
        ]])
    )
    return "WAITING_CONFIRMATION"

async def handle_purchase_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'cancel_purchase':
        await query.message.edit_text("تم إلغاء العملية.")
        return ConversationHandler.END
    
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT name, price FROM products WHERE id = ?', (context.user_data['product_id'],))
    product = c.fetchone()
    
    # خصم المبلغ من رصيد المستخدم
    c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?',
              (product[1], update.effective_user.id))
    conn.commit()
    
    # إرسال إشعار للمدير
    admin_message = f"""
طلب جديد:
المنتج: {product[0]}
السعر: {product[1]} ريال
بيانات الزبون: {context.user_data['customer_info']}
معرف المشتري: {update.effective_user.id}
"""
    c.execute('SELECT telegram_id FROM users WHERE id = 1')  # افتراض أن المدير له ID = 1
    admin_id = c.fetchone()[0]
    await context.bot.send_message(chat_id=admin_id, text=admin_message)
    
    conn.close()
    
    await query.message.edit_text("تم إتمام العملية بنجاح!")
    return ConversationHandler.END

# Flask routes
@app.route('/')
def admin_panel():
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT * FROM products')
    products = c.fetchall()
    c.execute('SELECT * FROM users')
    users = c.fetchall()
    conn.close()
    return render_template('admin.html', products=products, users=users)

@app.route('/add_product', methods=['POST'])
def add_product():
    name = request.form['name']
    price = float(request.form['price'])
    category = request.form['category']
    is_active = 'is_active' in request.form
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('INSERT INTO products (name, price, category, is_active) VALUES (?, ?, ?, ?)',
              (name, price, category, is_active))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/toggle_product', methods=['POST'])
def toggle_product():
    product_id = request.form['product_id']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE products SET is_active = NOT is_active WHERE id = ?', (product_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/edit_product', methods=['POST'])
def edit_product():
    product_id = request.form['product_id']
    name = request.form['name']
    price = float(request.form['price'])
    category = request.form['category']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE products SET name = ?, price = ?, category = ? WHERE id = ?',
              (name, price, category, product_id))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

async def send_notification(context: ContextTypes.DEFAULT_TYPE, message: str):
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT telegram_id FROM users')
    users = c.fetchall()
    conn.close()
    
    for user in users:
        try:
            await context.bot.send_message(chat_id=user[0], text=message)
        except:
            continue

@app.route('/send_notification', methods=['POST'])
def send_notification_route():
    message = request.form['message']
    user_id = request.form.get('user_id', None)
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    bot = telegram.Bot(token=bot_token)
    
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    
    try:
        if user_id:
            bot.send_message(chat_id=int(user_id), text=message)
        else:
            c.execute('SELECT telegram_id FROM users')
            users = c.fetchall()
            for user in users:
                try:
                    bot.send_message(chat_id=user[0], text=message)
                except Exception as e:
                    print(f"Error sending message to {user[0]}: {e}")
    except Exception as e:
        print(f"Error sending notification: {e}")
    
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/add_balance', methods=['POST'])
def add_balance():
    user_id = int(request.form['user_id'])
    amount = float(request.form['amount'])
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?',
              (amount, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

def run_flask():
    app.run(host='0.0.0.0', port=5000)

def run_bot():
    # Initialize bot
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        print("خطأ: لم يتم العثور على توكن البوت. الرجاء إضافته في Secrets")
        return
    
    print("جاري تشغيل البوت...")
    application = Application.builder().token(bot_token).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    
    # إضافة ConversationHandler للتعامل مع عملية الشراء
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_click)],
        states={
            "WAITING_CUSTOMER_INFO": [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_customer_info)],
            "WAITING_CONFIRMATION": [CallbackQueryHandler(handle_purchase_confirmation)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)]
    )
    
    application.add_handler(conv_handler)
    
    # Run bot
    application.run_polling()

if __name__ == '__main__':
    # Initialize database
    init_db()
    
    # Start Flask in a separate thread
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    
    # Run bot in main thread
    run_bot()
