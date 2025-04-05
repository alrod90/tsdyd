
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
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
            product_list = '\n'.join([f"{p[1]}: {p[2]} ريال" for p in products])
            await query.message.edit_text(f"المنتجات في قسم {category_names[category]}:\n{product_list}")
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
async def send_notification_route():
    message = request.form['message']
    # إنشاء تطبيق مؤقت للإرسال
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    application = Application.builder().token(bot_token).build()
    await send_notification(application, message)
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
    application.add_handler(CallbackQueryHandler(button_click))
    
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
