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
# Added for SMS functionality.  Replace with your actual gateway library.
import requests # Example using requests library. You might need a different library.

# Initialize Flask app
app = Flask(__name__)

# Database setup
def init_db():
    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    # إنشاء الجداول إذا لم تكن موجودة
    c.execute('''CREATE TABLE IF NOT EXISTS categories
                 (id INTEGER PRIMARY KEY, name TEXT, code TEXT, is_active BOOLEAN DEFAULT 1)''')
    
    # التحقق من وجود التصنيفات الافتراضية وإضافتها إذا لم تكن موجودة
    c.execute('SELECT code FROM categories WHERE code IN ("internet", "mobile", "landline")')
    existing_categories = set(row[0] for row in c.fetchall())
    
    default_categories = [
        ('إنترنت', 'internet', 1),
        ('جوال', 'mobile', 1),
        ('خط أرضي', 'landline', 1)
    ]
    
    for name, code, is_active in default_categories:
        if code not in existing_categories:
            c.execute('INSERT INTO categories (name, code, is_active) VALUES (?, ?, ?)',
                     (name, code, is_active))

    # إنشاء باقي الجداول
    c.execute('''CREATE TABLE IF NOT EXISTS products 
                 (id INTEGER PRIMARY KEY, name TEXT, category TEXT, is_active BOOLEAN DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY, telegram_id INTEGER, balance REAL, is_active BOOLEAN DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product_id INTEGER, amount REAL, 
                  customer_info TEXT, status TEXT DEFAULT 'pending', rejection_note TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, note TEXT)''')
    conn.commit()
    conn.close()

# Telegram bot commands
async def orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('''SELECT o.id, p.name, o.amount, o.status, o.rejection_note, o.created_at, o.note
                 FROM orders o 
                 JOIN products p ON o.product_id = p.id 
                 WHERE o.user_id = ? 
                 ORDER BY o.created_at DESC''', (user_id,))
    orders = c.fetchall()
    conn.close()

    if not orders:
        await update.message.reply_text("لا يوجد لديك طلبات.")
        return

    for order in orders:
        status_text = "قيد المعالجة" if order[3] == "pending" else "مقبول" if order[3] == "accepted" else "مرفوض"
        message = f"رقم الطلب: {order[0]}\n"
        message += f"الشركة: {order[1]}\n" # Changed from المنتج to الشركة
        message += f"المبلغ: {order[2]} ليرة سوري\n"
        message += f"الحالة: {status_text}\n"
        if order[3] == "rejected" and order[4]:
            message += f"سبب الرفض: {order[4]}\n"
        message += f"التاريخ: {order[5]}\n"
        if order[6]:
            message += f"ملاحظة: {order[6]}\n" # Added note display

        keyboard = []
        if order[3] == "pending":
            keyboard.append([InlineKeyboardButton("إلغاء الطلب", callback_data=f'cancel_order_{order[0]}')])

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(message, reply_markup=reply_markup)
        await update.message.reply_text("──────────────")

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

    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT name, code FROM categories WHERE is_active = 1')
    categories = c.fetchall()
    conn.close()

    keyboard = []
    row = []
    for i, category in enumerate(categories):
        row.append(InlineKeyboardButton(category[0], callback_data=f'cat_{category[1]}'))
        if (i + 1) % 3 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([
        InlineKeyboardButton("رصيدي", callback_data='balance'),
        InlineKeyboardButton("طلباتي", callback_data='my_orders')
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('مرحباً بك في متجرنا! الرجاء اختيار القسم:', reply_markup=reply_markup)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # التحقق من حالة المستخدم
    if query.data.startswith('cat_'):
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT is_active FROM users WHERE telegram_id = ?', (update.effective_user.id,))
        user = c.fetchone()
        conn.close()

        if not user or not user[0]:
            await query.message.edit_text("عذراً، حسابك معطل. يرجى التواصل مع المسؤول.")
            return

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
                keyboard.append([InlineKeyboardButton(f"{product[1]}", 
                                                    callback_data=f'buy_{product[0]}')]) #removed price
            keyboard.append([InlineKeyboardButton("رجوع", callback_data='back')])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(
                f"الشركات المتوفرة في قسم {category_names[category]}:", # Changed from المنتجات to الشركات
                reply_markup=reply_markup
            )
        else:
            await query.message.edit_text(f"لا توجد شركات متوفرة في قسم {category_names[category]}") # Changed from منتجات to شركات

    elif query.data == 'balance':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', (update.effective_user.id,))
        result = c.fetchone()
        balance = result[0] if result else 0
        conn.close()
        await query.message.edit_text(f"رصيدك الحالي: {balance} ليرة سوري")

    elif query.data == 'my_orders':
        keyboard = [
            [InlineKeyboardButton("البحث برقم الطلب", callback_data='search_order_number')],
            [InlineKeyboardButton("البحث ببيانات الزبون", callback_data='search_customer_info')],
            [InlineKeyboardButton("رجوع", callback_data='back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر طريقة البحث:", reply_markup=reply_markup)

    elif query.data == 'search_order_number':
        await query.message.edit_text("الرجاء إدخال رقم الطلب:")
        return "WAITING_ORDER_NUMBER"

    elif query.data == 'search_customer_info':
        await query.message.edit_text("الرجاء إدخال بيانات الزبون:")
        return "WAITING_SEARCH_CUSTOMER_INFO"

    elif query.data.startswith('cancel_order_'):
        order_id = int(query.data.split('_')[2])
        await query.message.edit_text("الرجاء إدخال سبب الإلغاء:")
        context.user_data['canceling_order_id'] = order_id
        return "WAITING_CANCEL_REASON"

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

        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT name FROM products WHERE id = ?', (product_id,))
        product_name = c.fetchone()[0]
        conn.close()

        context.user_data['product_name'] = product_name
        await query.message.edit_text("الرجاء إدخال بيانات الزبون:")
        return "WAITING_CUSTOMER_INFO"

async def handle_customer_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    customer_info = update.message.text
    context.user_data['customer_info'] = customer_info
    await update.message.reply_text("الرجاء إدخال المبلغ:")
    return "WAITING_AMOUNT"

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = float(update.message.text)
    context.user_data['amount'] = amount

    # التحقق من الرصيد
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT balance FROM users WHERE telegram_id = ?', (update.effective_user.id,))
    user_balance = c.fetchone()[0]
    conn.close()

    if amount > user_balance:
        await update.message.reply_text(f"عذراً، رصيدك غير كافي. رصيدك الحالي: {user_balance} ليرة سوري")
        return ConversationHandler.END
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT name FROM products WHERE id = ?', (context.user_data['product_id'],))
    product_name = c.fetchone()[0]
    conn.close()

    await update.message.reply_text(
        f"سيتم خصم {amount} ليرة سوري من رصيدك.\n"
        f"اضغط على تأكيد لإتمام العملية.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("تأكيد", callback_data='confirm_purchase'),
            InlineKeyboardButton("إلغاء", callback_data='cancel_purchase')
        ]])
    )
    return "WAITING_CONFIRMATION"


async def handle_search_order_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        order_number = int(update.message.text)
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        try:
            c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info, o.created_at, o.note, o.rejection_note
                         FROM orders o 
                         JOIN products p ON o.product_id = p.id 
                         WHERE o.id = ? AND o.user_id = ?''', (order_number, update.effective_user.id))
            order = c.fetchone()

            if order:
                status_text = "قيد المعالجة" if order[3] == "pending" else "مقبول" if order[3] == "accepted" else "مرفوض"
                message = f"""
تفاصيل الطلب:
رقم الطلب: {order[0]}
الشركة: {order[1]}
المبلغ: {order[2]} ليرة سوري
الحالة: {status_text}"""

                if order[3] == "rejected" and order[7]:  # إضافة سبب الرفض
                    message += f"\nسبب الرفض: {order[7]}"

                message += f"""
بيانات الزبون: {order[4]}
التاريخ: {order[5]}"""

                if order[6]:
                    message += f"\nملاحظة: {order[6]}"

                keyboard = [[InlineKeyboardButton("رجوع", callback_data='my_orders')]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(message, reply_markup=reply_markup)
            else:
                await update.message.reply_text("لم يتم العثور على الطلب")
        finally:
            conn.close()
    except ValueError:
        await update.message.reply_text("الرجاء إدخال رقم صحيح")
    return ConversationHandler.END

async def handle_cancel_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cancel_reason = update.message.text
    order_id = context.user_data.get('canceling_order_id')

    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    # استرجاع معلومات الطلب
    c.execute('SELECT amount, user_id FROM orders WHERE id = ?', (order_id,))
    order = c.fetchone()

    if order:
        # إعادة المبلغ للمستخدم
        c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?',
                  (order[0], order[1]))

        # تحديث حالة الطلب
        c.execute('UPDATE orders SET status = ?, rejection_note = ? WHERE id = ?',
                 ('cancelled', f'تم الإلغاء من قبل المستخدم. السبب: {cancel_reason}', order_id))

        conn.commit()

        # إرسال إشعار للمدير
        admin_message = f"""
تم إلغاء الطلب من قبل المستخدم
رقم الطلب: {order_id}
سبب الإلغاء: {cancel_reason}
"""
        try:
            response = requests.post("YOUR_SMS_GATEWAY_URL", 
                                  data={"to": "+963938074766", 
                                       "message": admin_message})
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"Error sending SMS: {e}")

        await update.message.reply_text("تم إلغاء الطلب بنجاح وتمت إعادة المبلغ إلى رصيدك.")
    else:
        await update.message.reply_text("عذراً، لم يتم العثور على الطلب.")

    conn.close()
    return ConversationHandler.END

async def handle_search_customer_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    customer_info = update.message.text
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info, o.created_at, o.note
                 FROM orders o 
                 JOIN products p ON o.product_id = p.id 
                 WHERE o.customer_info LIKE ?''', ('%' + customer_info + '%',))
    orders = c.fetchall()
    conn.close()

    if orders:
        message = "الطلبات المطابقة:\n\n"
        for order in orders:
            status_text = "قيد المعالجة" if order[3] == "pending" else "مقبول" if order[3] == "accepted" else "مرفوض"
            message += f"""
رقم الطلب: {order[0]}
الشركة: {order[1]} # Changed from المنتج to الشركة
المبلغ: {order[2]} ليرة سوري
الحالة: {status_text}
بيانات الزبون: {order[4]}
التاريخ: {order[5]}
"""
            if order[6]:
                message += f"ملاحظة: {order[6]}\n" # Added note display
            message += "──────────────\n"
        keyboard = [[InlineKeyboardButton("رجوع", callback_data='my_orders')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup)
    else:
        await update.message.reply_text("لم يتم العثور على طلبات مطابقة")
    return ConversationHandler.END

async def handle_purchase_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'cancel_purchase':
        await query.message.edit_text("تم إلغاء العملية.")
        return ConversationHandler.END

    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT name FROM products WHERE id = ?', (context.user_data['product_id'],))
    product_name = c.fetchone()[0]
    amount = context.user_data['amount']
    customer_info = context.user_data['customer_info']

    # خصم المبلغ من رصيد المستخدم وإنشاء الطلب
    c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?',
              (amount, update.effective_user.id))
    c.execute('INSERT INTO orders (user_id, product_id, amount, customer_info) VALUES (?, ?, ?, ?)',
              (update.effective_user.id, context.user_data['product_id'], amount, customer_info))
    order_id = c.lastrowid
    conn.commit()

    # إرسال إشعار للمدير
    admin_message = f"""
طلب جديد:
الشركة: {product_name} # Changed from المنتج to الشركة
المبلغ: {amount} ليرة سوري
بيانات الزبون: {customer_info}
معرف المشتري: {update.effective_user.id}
"""
    #Send SMS -  Replace with your SMS gateway API call
    try:
        #Example using requests - replace with your actual API call and credentials
        response = requests.post("YOUR_SMS_GATEWAY_URL", data={"to": "+96393807466", "message": admin_message})
        response.raise_for_status() # Raise an exception for bad status codes
        print("SMS sent successfully!")

    except requests.exceptions.RequestException as e:
        print(f"Error sending SMS: {e}")


    c.execute('SELECT telegram_id FROM users WHERE id = 1')  # افتراض أن المدير له ID = 1
    admin_id = c.fetchone()[0]
    await context.bot.send_message(chat_id=admin_id, text=admin_message)

    conn.close()

    await query.message.edit_text("تم تسجيل طلبك مع رقم الطلب وبيانات") # Changed success message
    return ConversationHandler.END

# Flask routes
@app.route('/')
def admin_panel():
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT * FROM categories')
    categories = c.fetchall()
    c.execute('SELECT * FROM products')
    products = c.fetchall()
    c.execute('SELECT * FROM users')
    users = c.fetchall()
    c.execute('''SELECT o.id, o.user_id, p.name, o.amount, o.customer_info, o.status, o.created_at, o.note
                 FROM orders o 
                 JOIN products p ON o.product_id = p.id 
                 ORDER BY o.created_at DESC''')
    orders = c.fetchall()
    conn.close()
    return render_template('admin.html', products=products, users=users, orders=orders)

@app.route('/add_product', methods=['POST'])
def add_product():
    name = request.form['name']
    category = request.form['category']
    is_active = 'is_active' in request.form
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('INSERT INTO products (name, category, is_active) VALUES (?, ?, ?)',
              (name, category, is_active))
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

@app.route('/delete_product', methods=['POST'])
def delete_product():
    product_id = request.form['product_id']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('DELETE FROM products WHERE id = ?', (product_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/edit_product', methods=['POST'])
def edit_product():
    product_id = request.form['product_id']
    name = request.form['name']
    category = request.form['category']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE products SET name = ?, category = ? WHERE id = ?',
              (name, category, product_id))
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

@app.route('/handle_order', methods=['POST'])
def handle_order():
    conn = None
    try:
        order_id = request.form.get('order_id')
        action = request.form.get('action')
        rejection_note = request.form.get('rejection_note', '')

        if not order_id or not action:
            return "بيانات غير صحيحة", 400

        conn = sqlite3.connect('store.db')
        c = conn.cursor()

        # التحقق من وجود الطلب
        c.execute('SELECT user_id, amount FROM orders WHERE id = ?', (order_id,))
        order = c.fetchone()

        if not order:
            if conn:
                conn.close()
            return "الطلب غير موجود", 404

        if action == 'reject':
            if not rejection_note and action == 'reject':
                if conn:
                    conn.close()
                return "يجب إدخال سبب الرفض", 400

            # إعادة المبلغ للمستخدم
            c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?',
                    (order[1], order[0]))
            # تحديث حالة الطلب
            c.execute('UPDATE orders SET status = ?, rejection_note = ? WHERE id = ?',
                    ('rejected', rejection_note, order_id))
        elif action == 'accept':
            c.execute('UPDATE orders SET status = ? WHERE id = ?', 
                    ('accepted', order_id))

        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))

    except Exception as e:
        print(f"Error in handle_order: {str(e)}")
        if conn:
            conn.close()
        return f"حدث خطأ في معالجة الطلب: {str(e)}", 500

@app.route('/delete_order', methods=['POST'])
def delete_order():
    order_id = request.form['order_id']

    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    c.execute('SELECT user_id, amount, status FROM orders WHERE id = ?', (order_id,))
    order = c.fetchone()

    if order[2] != 'accepted':  # إعادة المبلغ إذا لم يكن الطلب مقبولاً
        c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?',
                  (order[1], order[0]))

    c.execute('DELETE FROM orders WHERE id = ?', (order_id,))
    conn.commit()
    conn.close()

    return redirect(url_for('admin_panel'))

    return redirect(url_for('admin_panel'))

@app.route('/add_category', methods=['POST'])
def add_category():
    name = request.form['name']
    code = request.form['code']
    is_active = 'is_active' in request.form
    
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('INSERT INTO categories (name, code, is_active) VALUES (?, ?, ?)',
              (name, code, is_active))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/edit_category', methods=['POST'])
def edit_category():
    category_id = request.form['category_id']
    name = request.form['name']
    code = request.form['code']
    is_active = 'is_active' in request.form
    
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE categories SET name = ?, code = ?, is_active = ? WHERE id = ?',
              (name, code, is_active, category_id))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/toggle_category', methods=['POST'])
def toggle_category():
    category_id = request.form['category_id']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE categories SET is_active = NOT is_active WHERE id = ?', (category_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/delete_category', methods=['POST'])
def delete_category():
    category_id = request.form['category_id']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('DELETE FROM categories WHERE id = ?', (category_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/edit_user', methods=['POST'])
def edit_user():
    user_id = request.form['user_id']
    telegram_id = request.form['telegram_id']
    balance = float(request.form['balance'])
    is_active = 'is_active' in request.form

    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE users SET telegram_id = ?, balance = ?, is_active = ? WHERE id = ?',
              (telegram_id, balance, is_active, user_id))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))


@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    user_id = request.form['user_id']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE users SET is_active = NOT is_active WHERE id = ?', (user_id,))
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
    application.add_handler(CommandHandler("orders", orders))

    # إضافة ConversationHandler للتعامل مع عملية الشراء
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_click)],
        states={
            "WAITING_CUSTOMER_INFO": [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_customer_info)],
            "WAITING_AMOUNT": [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount)],
            "WAITING_CONFIRMATION": [CallbackQueryHandler(handle_purchase_confirmation)],
            "WAITING_ORDER_NUMBER": [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_order_number)],
            "WAITING_SEARCH_CUSTOMER_INFO": [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_customer_info)],
            "WAITING_CANCEL_REASON": [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cancel_reason)]
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