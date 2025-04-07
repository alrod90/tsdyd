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

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # مفتاح سري للجلسة
from threading import Thread
from functools import wraps
# Added for SMS functionality.  Replace with your actual gateway library.
import requests # Example using requests library. You might need a different library.

# Initialize Flask app
app = Flask(__name__)

# Database setup
def sync_deployed_db():
    """مزامنة قاعدة البيانات من النسخة المنشورة"""
    try:
        # البحث عن أحدث نسخة احتياطية
        backup_folders = [d for d in os.listdir('.') if d.startswith('backup_') and os.path.isdir(d)]
        if not backup_folders:
            raise Exception("لم يتم العثور على مجلد النسخ الاحتياطية")
            
        latest_backup = max(backup_folders)
        backup_db = f'{latest_backup}/store.db'
        
        if not os.path.exists(backup_db):
            raise Exception("لم يتم العثور على قاعدة البيانات في النسخة الاحتياطية")

        # إغلاق أي اتصالات مفتوحة
        try:
            conn = sqlite3.connect('store.db')
            conn.close()
        except:
            pass

        # فتح الاتصال بقواعد البيانات
        backup_conn = sqlite3.connect(backup_db)
        local_conn = sqlite3.connect('store.db')
        
        # نقل الطلبات الجديدة
        backup_conn.execute("ATTACH DATABASE 'store.db' AS local")
        backup_conn.execute("""
            INSERT OR IGNORE INTO local.orders 
            SELECT * FROM orders 
            WHERE id NOT IN (SELECT id FROM local.orders)
        """)
        
        # تحديث حالة الطلبات الموجودة
        backup_conn.execute("""
            UPDATE local.orders 
            SET status = orders.status,
                note = orders.note,
                rejection_note = orders.rejection_note
            FROM orders 
            WHERE local.orders.id = orders.id
        """)
        
        backup_conn.commit()
        backup_conn.close()
        local_conn.close()
        
        print(f"تم تحديث الطلبات من النسخة المنشورة: {backup_db}")
        
    except Exception as e:
        print(f"خطأ في مزامنة قاعدة البيانات: {str(e)}")

def init_db():
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    # ضبط المنطقة الزمنية لقاعدة البيانات وتنسيق التاريخ
    c.execute("PRAGMA timezone = '+03:00'")
    c.execute("""
        CREATE TRIGGER IF NOT EXISTS update_timestamp 
        AFTER INSERT ON orders 
        BEGIN 
            UPDATE orders 
            SET created_at = datetime(datetime('now', '+3 hours')) 
            WHERE id = NEW.id; 
        END;
    """)

    # إنشاء الجداول إذا لم تكن موجودة
    c.execute('''CREATE TABLE IF NOT EXISTS products 
                 (id INTEGER PRIMARY KEY, name TEXT, category TEXT, is_active BOOLEAN DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY, telegram_id INTEGER, balance REAL, 
                  phone_number TEXT, is_active BOOLEAN DEFAULT 1, note TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product_id INTEGER, amount REAL, 
                  customer_info TEXT, status TEXT DEFAULT 'pending', rejection_note TEXT,
                  created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours')), note TEXT)''')
    conn.commit()
    conn.close()

# Telegram bot commands
async def orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    # التحقق من صلاحيات المدير
    c.execute('SELECT id FROM users WHERE telegram_id = ? AND id = 1', (user_id,))
    is_admin = c.fetchone() is not None

    if is_admin:
        # المدير يمكنه رؤية جميع الطلبات
        c.execute('''SELECT o.id, p.name, o.amount, o.status, o.rejection_note, o.created_at, o.note, u.telegram_id
                     FROM orders o 
                     JOIN products p ON o.product_id = p.id 
                     JOIN users u ON o.user_id = u.telegram_id
                     ORDER BY o.created_at DESC''')
    else:
        # المستخدم العادي يرى طلباته فقط
        c.execute('''SELECT o.id, p.name, o.amount, o.status, o.rejection_note, o.created_at, o.note, u.telegram_id
                     FROM orders o 
                     JOIN products p ON o.product_id = p.id 
                     JOIN users u ON o.user_id = u.telegram_id
                     WHERE o.user_id = ? 
                     ORDER BY o.created_at DESC''', (user_id,))

    orders = c.fetchall()
    conn.close()

    if not orders:
        keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("لا يوجد لديك طلبات.", reply_markup=reply_markup)
        return

    for order in orders:
        status_text = "قيد المعالجة" if order[3] == "pending" else "تمت العملية بنجاح" if order[3] == "accepted" else "مرفوض"
        message = f"رقم الطلب: {order[0]}\n"
        message += f"الشركة: {order[1]}\n"
        message += f"المبلغ: {order[2]} ليرة سوري\n"
        if user_id == 1:  # إذا كان المستخدم هو المدير
            message += f"معرف المستخدم: {order[7]}\n"
        message += f"الحالة: {status_text}\n"
        if order[3] == "rejected" and order[4]:
            message += f"سبب الرفض: {order[4]}\n"
        message += f"التاريخ: {order[5]}\n"

        keyboard = []
        if order[3] == "pending":
            keyboard.append([InlineKeyboardButton("إلغاء الطلب", callback_data=f'cancel_order_{order[0]}')])
        keyboard.append([InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]) #added back button

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(message, reply_markup=reply_markup)
        await update.message.reply_text("──────────────")

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    # التحقق من صلاحيات المدير
    c.execute('SELECT id FROM users WHERE telegram_id = ? AND id = 1', (user_id,))
    is_admin = c.fetchone() is not None

    if not is_admin:
        await update.message.reply_text("عذراً، هذا الأمر متاح فقط للمدير")
        return

    keyboard = [
        [InlineKeyboardButton("إدارة المنتجات", callback_data='manage_products')],
        [InlineKeyboardButton("إدارة المستخدمين", callback_data='manage_users')],
        [InlineKeyboardButton("إدارة الطلبات", callback_data='manage_orders')],
        [InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')] #added back button
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("مرحباً بك في لوحة التحكم:", reply_markup=reply_markup)

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

    welcome_message = f"""مرحبا بك في نظام تسديد الفواتير
معرف التيليجرام الخاص بك هو: {user_id}
يمكنك استخدام هذا المعرف للتواصل مع الإدارة.
"""
    await update.message.reply_text(welcome_message)

    keyboard = [
        [
            InlineKeyboardButton("إنترنت", callback_data='cat_internet'),
            InlineKeyboardButton("جوال", callback_data='cat_mobile'),
            InlineKeyboardButton("خط أرضي", callback_data='cat_landline')
        ],
        [
            InlineKeyboardButton("البنوك", callback_data='cat_banks')
        ],
        [
            InlineKeyboardButton("رصيدي", callback_data='balance'),
            InlineKeyboardButton("طلباتي", callback_data='my_orders')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('اهلا بك في تسديد الفواتير الرجاء الاختيار علما ان مدة التسديد تتراوح بين 10 والساعتين عدا العطل والضغط يوجد تاخير والدوام من 9ص حتى 9 م', reply_markup=reply_markup)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith('cat_'):
        category = query.data.split('_')[1]
        category_names = {
            'internet': 'إنترنت',
            'mobile': 'جوال',
            'landline': 'خط أرضي',
            'banks': 'البنوك'
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
            keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]] #added back button
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(f"لا توجد شركات متوفرة في قسم {category_names[category]}", reply_markup=reply_markup) # Changed from منتجات to شركات

    elif query.data == 'balance':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', (update.effective_user.id,))
        result = c.fetchone()
        balance = result[0] if result else 0
        conn.close()
        keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]] #added back button
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(f"رصيدك الحالي: {balance} ليرة سوري", reply_markup=reply_markup)

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
            [
                InlineKeyboardButton("إنترنت", callback_data='cat_internet'),
                InlineKeyboardButton("جوال", callback_data='cat_mobile'),
                InlineKeyboardButton("خط أرضي", callback_data='cat_landline')
            ],
            [
                InlineKeyboardButton("البنوك", callback_data='cat_banks')
            ],
            [
                InlineKeyboardButton("رصيدي", callback_data='balance'),
                InlineKeyboardButton("طلباتي", callback_data='my_orders')
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text('اهلا بك في تسديد الفواتير الرجاء الاختيار علما ان مدة التسديد تتراوح بين 10 والساعتين عدا العطل والضغط يوجد تاخير والدوام من 9ص حتى 9 م', reply_markup=reply_markup)

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

        # التحقق من صلاحيات المدير
        c.execute('SELECT id FROM users WHERE telegram_id = ? AND id = 1', (update.effective_user.id,))
        is_admin = c.fetchone() is not None

        try:
            if is_admin:
                # المدير يمكنه البحث في جميع الطلبات
                c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info, o.created_at, o.note, o.rejection_note, u.telegram_id
                            FROM orders o 
                            JOIN products p ON o.product_id = p.id 
                            JOIN users u ON o.user_id = u.telegram_id
                            WHERE o.id = ?''', (order_number,))
            else:
                # المستخدم العادي يبحث في طلباته فقط
                c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info, o.created_at, o.note, o.rejection_note, u.telegram_id
                            FROM orders o 
                            JOIN products p ON o.product_id = p.id 
                            JOIN users u ON o.user_id = u.telegram_id
                            WHERE o.id = ? AND o.user_id = ?''', (order_number, update.effective_user.id))
            order = c.fetchone()

            if order:
                status_text = "قيد المعالجة" if order[3] == "pending" else "تمت العملية بنجاح" if order[3] == "accepted" else "مرفوض"
                message = f"""
تفاصيل الطلب:
رقم الطلب: {order[0]}
الشركة: {order[1]}
المبلغ: {order[2]} ليرة سوري
الحالة: {status_text}
بيانات الزبون: {order[4]}
التاريخ: {order[5]}"""

                if order[3] == "rejected" and order[7]:  # إضافة سبب الرفض
                    message += f"\nسبب الرفض: {order[7]}"

                if order[6]:  # إضافة الملاحظة إذا وجدت
                    message += f"\nملاحظة: {order[6]}"

                # إضافة معرف التيليجرام فقط للمدير
                if is_admin:
                    message += f"\nمعرف التيليجرام لمقدم الطلب: {order[8]}"


                keyboard = [[InlineKeyboardButton("رجوع", callback_data='my_orders')]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(message, reply_markup=reply_markup)
            else:
                keyboard = [
                    [InlineKeyboardButton("التأكد من البيانات", callback_data='search_order_number')],
                    [InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text("لم يتم العثور على الطلب. هل تريد إدخال رقم طلب آخر؟", reply_markup=reply_markup)
        finally:
            conn.close()
    except ValueError:
        keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]] #added back button
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("الرجاء إدخال رقم صحيح", reply_markup=reply_markup)
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
        keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]] #added back button
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("عذراً، لم يتم العثور على الطلب.", reply_markup=reply_markup)

    conn.close()
    return ConversationHandler.END

async def handle_search_customer_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    customer_info = update.message.text
    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    # التحقق من صلاحيات المدير
    c.execute('SELECT id FROM users WHERE telegram_id = ? AND id = 1', (update.effective_user.id,))
    is_admin = c.fetchone() is not None

    if is_admin:
        # المدير يمكنه البحث في جميع الطلبات
        c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info, o.created_at, o.note, u.telegram_id
                     FROM orders o 
                     JOIN products p ON o.product_id = p.id 
                     JOIN users u ON o.user_id = u.telegram_id
                     WHERE o.customer_info LIKE ?''', ('%' + customer_info + '%',))
    else:
        # المستخدم العادي يبحث في طلباته فقط
        c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info, o.created_at, o.note, u.telegram_id
                     FROM orders o 
                     JOIN products p ON o.product_id = p.id 
                     JOIN users u ON o.user_id = u.telegram_id
                     WHERE o.customer_info LIKE ? AND o.user_id = ?''', ('%' + customer_info + '%', update.effective_user.id))
    orders = c.fetchall()
    conn.close()

    if orders:
        message = "الطلبات المطابقة:\n\n"
        for order in orders:
            status_text = "قيد المعالجة" if order[3] == "pending" else "تمت العملية بنجاح" if order[3] == "accepted" else "مرفوض"
            message += f"""
رقم الطلب: {order[0]}
الشركة: {order[1]} # Changed from المنتج to الشركة
المبلغ: {order[2]} ليرة سوري
الحالة: {status_text}
بيانات الزبون: {order[4]}
التاريخ: {order[5]}
"""
            message += "──────────────\n"
        keyboard = [[InlineKeyboardButton("رجوع", callback_data='my_orders')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup)
    else:
        keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]] #added back button
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("لم يتم العثور على طلبات مطابقة", reply_markup=reply_markup)
    return ConversationHandler.END

async def handle_purchase_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'cancel_purchase':
        keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("تم إلغاء العملية.", reply_markup=reply_markup)
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

    confirmation_message = f"""
تم تسجيل طلبك بنجاح!
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ: {amount} ليرة سوري
بيانات الزبون: {customer_info}
"""
    keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(confirmation_message, reply_markup=reply_markup)
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
    # التحقق من صلاحيات المستخدم
    c.execute('SELECT telegram_id FROM users WHERE id = 1')  # المدير له ID = 1
    admin_id = c.fetchone()

    if admin_id and admin_id[0]:  # إذا كان المستخدم هو المدير
        c.execute('''SELECT o.id, o.user_id, p.name, o.amount, o.customer_info, o.status, o.created_at, o.note
                     FROM orders o 
                     JOIN products p ON o.product_id = p.id 
                     ORDER BY o.created_at DESC''')
    else:  # إذا كان مستخدم عادي
        user_telegram_id = session.get('user_telegram_id')
        c.execute('''SELECT o.id, o.user_id, p.name, o.amount, o.customer_info, o.status, o.created_at, o.note
                     FROM orders o 
                     JOIN products p ON o.product_id = p.id 
                     WHERE o.user_id = ?
                     ORDER BY o.created_at DESC''', (user_telegram_id,))
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

async def send_notification(context: ContextTypes.DEFAULT_TYPE, message: str, user_id=None, is_important=False):
    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    if user_id:
        users = [(user_id,)]
    else:
        c.execute('SELECT telegram_id FROM users WHERE is_active = 1')
        users = c.fetchall()

    # إرسال عبر تيليجرام أولاً
    for user in users:
        success = False
        retry_count = 3

        while retry_count > 0 and not success:
            try:
                # محاولة إرسال رسالة مع إشعار صوتي
                await context.bot.send_message(
                    chat_id=user[0],
                    text=message,
                    disable_notification=False,
                    protect_content=True
                )
                success = True
            except Exception as e:
                print(f"Error sending Telegram message to {user[0]}: {str(e)}")
                retry_count -= 1
                await asyncio.sleep(1)

        # إذا فشل الإرسال عبر تيليجرام وكان الإشعار مهماً، نرسل SMS
        if not success and is_important:
            try:
                # استرجاع رقم الهاتف من قاعدة البيانات
                c.execute('SELECT phone_number FROM users WHERE telegram_id = ?', (user[0],))
                phone_result = c.fetchone()

                if phone_result and phone_result[0]:
                    # إرسال SMS عبر خدمة SMS
                    response = requests.post(
                        "YOUR_SMS_GATEWAY_URL",
                        data={
                            "to": phone_result[0],
                            "message": f"إشعار مهم: {message}"
                        }
                    )
                    response.raise_for_status()
            except Exception as e:
                print(f"Error sending SMS to {user[0]}: {str(e)}")

    conn.close()

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
                    print(f"Error sending messageto {user[0]}: {e}")
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

    # تحديث الرصيد
    c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?',
              (amount, user_id))

    # الحصول على الرصيد الجديد
    c.execute('SELECT balance FROM users WHERE telegram_id = ?', (user_id,))
    new_balance = c.fetchone()[0]

    conn.commit()
    conn.close()

    # إرسال إشعار للمستخدم
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    bot = telegram.Bot(token=bot_token)
    notification_message = f"""💰 تم إضافة رصيد لحسابك
المبلغ المضاف: {amount} ليرة سوري
رصيدك الحالي: {new_balance} ليرة سوري"""

    try:
        asyncio.run(bot.send_message(
            chat_id=user_id,
            text=notification_message,
            parse_mode='HTML'
        ))
    except Exception as e:
        print(f"خطأ في إرسال الإشعار: {str(e)}")

    return redirect(url_for('admin_panel'))

@app.route('/edit_user', methods=['POST'])
def edit_user():
    try:
        user_id = request.form['user_id']
        new_balance = float(request.form['balance'])
        conn = sqlite3.connect('store.db')
        c = conn.cursor()

        # الحصول على الرصيد القديم
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', (user_id,))
        old_balance = c.fetchone()[0]

        # تحديث الرصيد
        c.execute('UPDATE users SET balance = ? WHERE telegram_id = ?',
                  (new_balance, user_id))
        conn.commit()
        conn.close()

        # إرسال إشعار للمستخدم
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        bot = telegram.Bot(token=bot_token)
        notification_message = f"""💰 تم تعديل رصيدك
الرصيد السابق: {old_balance} ليرة سوري
الرصيد الجديد: {new_balance} ليرة سوري"""

        try:
            asyncio.run(bot.send_message(
                chat_id=user_id,
                text=notification_message,
                parse_mode='HTML'
            ))
        except Exception as e:
            print(f"خطأ في إرسال الإشعار: {str(e)}")

        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in edit_user: {str(e)}")
        return "حدث خطأ في تحديث الرصيد", 500

@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    user_id = request.form['user_id']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE users SET is_active = NOT is_active WHERE telegram_id = ?',
              (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/change_order_status', methods=['POST'])
def change_order_status():
    try:
        order_id = request.form.get('order_id')
        new_status = request.form.get('new_status')
        note = request.form.get('note', '')
        rejection_note = request.form.get('rejection_note', '')

        if not order_id or not new_status:
            return "بيانات غير صحيحة", 400

        conn = sqlite3.connect('store.db')
        c = conn.cursor()

        # استرجاع معلومات الطلب الحالية
        c.execute('SELECT status, user_id, amount FROM orders WHERE id = ?', (order_id,))
        current_order = c.fetchone()

        if not current_order:
            conn.close()
            return "الطلب غير موجود", 404

        current_status = current_order[0]
        user_id = current_order[1]
        amount = current_order[2]

        # التحقق من رصيد المستخدم عند التغيير من مرفوض إلى قيد المعالجة أو مقبول
        if current_status == 'rejected' and (new_status == 'pending' or new_status == 'accepted'):
            c.execute('SELECT balance FROM users WHERE telegram_id = ?', (user_id,))
            user_balance = c.fetchone()[0]
            if user_balance < amount:
                conn.close()
                return "رصيد المستخدم غير كافي لتغيير حالة الطلب", 400
            # خصم المبلغ
            c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?',
                     (amount, user_id))

        # إعادة المبلغ عند التغيير إلى مرفوض
        elif current_status != 'rejected' and new_status == 'rejected':
            c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?',
                     (amount, user_id))

        # تحديث حالة الطلب
        c.execute('UPDATE orders SET status = ?, note = ?, rejection_note = ? WHERE id = ?',
                 (new_status, note, rejection_note if new_status == 'rejected' else None, order_id))

        # استرجاع معلومات المنتج
        c.execute('SELECT p.name FROM orders o JOIN products p ON o.product_id = p.id WHERE o.id = ?', (order_id,))
        product_name = c.fetchone()[0]

        # إعداد رسالة الإشعار
        if new_status == "accepted":
            notification_message = f"""✅ تم قبول طلبك!
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ: {amount} ليرة سوري"""
        elif new_status == "rejected":
            notification_message = f"""❌ تم رفض طلبك وإعادة المبلغ لرصيدك
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ المعاد لرصيدك: {amount} ليرة سوري"""
            if rejection_note:
                notification_message += f"\nسبب الرفض: {rejection_note}"

            # إضافة الرصيد الحالي بعد الإعادة
            c.execute('SELECT balance FROM users WHERE telegram_id = ?', (user_id,))
            current_balance = c.fetchone()[0]
            notification_message += f"\n\nرصيدك الحالي: {current_balance} ليرة سوري"
        else:
            notification_message = f"""🕒 تم تحديث حالة طلبك
رقم الطلب: {order_id}
الشركة: {product_name}
الحالة: قيد المعالجة"""

        if note:
            notification_message += f"\nملاحظة: {note}"

        # إرسال الإشعار للمستخدم
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        bot = telegram.Bot(token=bot_token)
        try:
            asyncio.run(bot.send_message(
                chat_id=user_id,
                text=notification_message,
                parse_mode='HTML'
            ))
        except Exception as e:
            print(f"خطأ في إرسال الإشعار: {str(e)}")

        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))

    except Exception as e:
        print(f"Error in change_order_status: {str(e)}")
        return f"حدث خطأ في تغيير حالة الطلب: {str(e)}", 500

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

        # استرجاع معلومات الطلب والمنتج
        c.execute('''
            SELECT o.user_id, o.amount, p.name, u.balance 
            FROM orders o 
            JOIN products p ON o.product_id = p.id 
            JOIN users u ON o.user_id = u.telegram_id 
            WHERE o.id = ?
        ''', (order_id,))
        order = c.fetchone()

        if not order:
            if conn:
                conn.close()
            return "الطلب غير موجود", 404

        user_id = order[0]
        amount = order[1]
        product_name = order[2]
        current_balance = order[3]

        if action == 'reject':
            if not rejection_note and action == 'reject':
                if conn:
                    conn.close()
                return "يجب إدخال سبب الرفض", 400

            note = request.form.get('note', '')

            # إعادة المبلغ للمستخدم
            c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?',
                    (amount, user_id))
            # تحديث حالة الطلب مع الملاحظة
            c.execute('UPDATE orders SET status = ?, rejection_note = ?, note = ? WHERE id = ?',
                    ('rejected', rejection_note, note, order_id))

            # إعداد رسالة الإشعار للرفض
            notification_message = f"""❌ تم رفض طلبك وإعادة المبلغ لرصيدك
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ المعاد لرصيدك: {amount} ليرة سوري
سبب الرفض: {rejection_note}
رصيدك الحالي: {current_balance + amount} ليرة سوري"""

        elif action == 'accept':
            c.execute('UPDATE orders SET status = ? WHERE id = ?', 
                    ('accepted', order_id))

            # إعداد رسالة الإشعار للقبول
            notification_message = f"""✅ تم قبول طلبك!
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ: {amount} ليرة سوري"""

        # إرسال الإشعار للمستخدم
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        bot = telegram.Bot(token=bot_token)
        try:
            asyncio.run(bot.send_message(
                chat_id=user_id,
                text=notification_message,
                parse_mode='HTML'
            ))
        except Exception as e:
            print(f"خطأ في إرسال الإشعار: {str(e)}")

        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))

    except Exception as e:
        print(f"Error in handle_order: {str(e)}")
        if conn:
            conn.close()
        return f"حدث خطأ في معالجة الطلب: {str(e)}", 500

@app.route('/edit_order_amount', methods=['POST'])
def edit_order_amount():
    try:
        order_id = request.form['order_id']
        new_amount = float(request.form['new_amount'])

        conn = sqlite3.connect('store.db')
        c = conn.cursor()

        # استرجاع معلومات الطلب الحالية
        c.execute('SELECT amount, user_id, status FROM orders WHERE id = ?', (order_id,))
        current_order = c.fetchone()

        if not current_order:
            conn.close()
            return "الطلب غير موجود", 404

        current_amount = current_order[0]
        user_id = current_order[1]
        status = current_order[2]

        # إذا كان الطلب مقبولاً أو قيد المعالجة، نتعامل مع الرصيد
        if status != 'rejected':
            amount_diff = new_amount - current_amount

            if amount_diff > 0:  # إذا كان المبلغ الجديد أكبر
                # التحقق من الرصيد
                c.execute('SELECT balance FROM users WHERE telegram_id = ?', (user_id,))
                user_balance = c.fetchone()[0]

                if user_balance < amount_diff:
                    conn.close()
                    return "رصيد المستخدم غير كافي للتعديل", 400

                # خصم الفرق من رصيد المستخدم
                c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?',
                         (amount_diff, user_id))
            elif amount_diff < 0:  # إذا كان المبلغ الجديد أقل
                # إعادة الفرق لرصيد المستخدم
                c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?',
                         (-amount_diff, user_id))

        # تحديث مبلغ الطلب
        c.execute('UPDATE orders SET amount = ? WHERE id = ?', (new_amount, order_id))

        # إرسال إشعار للمستخدم
        notification_message = f"تم تعديل مبلغ الطلب رقم {order_id}\nالمبلغ الجديد: {new_amount} ليرة سوري"

        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        bot = telegram.Bot(token=bot_token)
        asyncio.run(bot.send_message(chat_id=user_id, text=notification_message))

        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))

    except Exception as e:
        print(f"Error in edit_order_amount: {str(e)}")
        return f"حدث خطأ في تعديل مبلغ الطلب: {str(e)}", 500

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

def get_db_connection():
    deployed_db = 'backup_20250407_094844/store.db'
    if not os.path.exists(deployed_db):
        raise Exception("لم يتم العثور على قاعدة البيانات المنشورة")
    conn = sqlite3.connect(deployed_db)
    conn.execute("PRAGMA timezone = '+03:00'")
    return conn

def run_flask():
    app.run(host='0.0.0.0', port=5000, threaded=True)

def run_bot():
    try:
        # Initialize bot
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not bot_token:
            print("خطأ: لم يتم العثور على توكن البوت. الرجاء إضافته في Secrets")
            return

        print("جاري تشغيل البوت...")
        application = Application.builder().token(bot_token).build()



    # Add handlers
    application.add_handler(CommandHandler("orders", orders))
    application.add_handler(CommandHandler("admin", admin_panel_command))

    # إضافة ConversationHandler للتعامل مع عملية الشراء
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(button_click)
        ],
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
    # ضبط المنطقة الزمنية
    os.environ['TZ'] = 'Asia/Damascus'
    
    try:
        import time
        time.tzset()
    except AttributeError:
        pass  # للتوافق مع أنظمة Windows

    # Initialize database
    init_db()

    # تشغيل Flask في خلفية البرنامج
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True  # جعل الخيط يتوقف عند إغلاق البرنامج
    flask_thread.start()

    # تشغيل البوت
    run_bot()