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
    """التحقق من وجود قاعدة البيانات"""
    try:
        if not os.path.exists('store.db'):
            # إنشاء قاعدة بيانات جديدة فقط إذا لم تكن موجودة
            conn = sqlite3.connect('store.db')
            c = conn.cursor()
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
            print("تم إنشاء قاعدة بيانات جديدة")
    except Exception as e:
        print(f"خطأ في التحقق من قاعدة البيانات: {str(e)}")

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
                  phone_number TEXT, is_active BOOLEAN DEFAULT 1, note TEXT,
                  is_distributor BOOLEAN DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product_id INTEGER, amount REAL, 
                  customer_info TEXT, status TEXT DEFAULT 'pending', rejection_note TEXT,
                  created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours')), note TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS categories
                     (id INTEGER PRIMARY KEY, name TEXT, identifier TEXT, is_active BOOLEAN DEFAULT 1)''')

    # تحديث حالة المنتجات عند تغيير حالة القسم
    c.execute('''CREATE TRIGGER IF NOT EXISTS update_products_status 
                 AFTER UPDATE ON categories
                 FOR EACH ROW
                 BEGIN
                     UPDATE products SET is_active = NEW.is_active 
                     WHERE category = NEW.identifier;
                 END;''')

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
    try:
        user_id = update.effective_user.id
        conn = sqlite3.connect('store.db')
        c = conn.cursor()

        # تحسين التحقق من صلاحيات المدير
        c.execute('SELECT id, telegram_id FROM users WHERE telegram_id = ? AND id = 1', (user_id,))
        admin = c.fetchone()
        conn.close()

        if not admin or admin[1] != user_id:
            await update.message.reply_text("عذراً، هذا الأمر متاح فقط للمدير")
            return

        keyboard = [
            [
                InlineKeyboardButton("المنتجات", callback_data='products_menu'),
                InlineKeyboardButton("الطلبات", callback_data='orders_menu')
            ],
            [
                InlineKeyboardButton("المستخدمين", callback_data='users_menu'),
                InlineKeyboardButton("الأرصدة", callback_data='balance_menu')
            ],
            [InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("مرحباً بك في لوحة التحكم:", reply_markup=reply_markup)

    except Exception as e:
        print(f"خطأ في لوحة التحكم: {str(e)}")
        await update.message.reply_text("حدث خطأ في الوصول للوحة التحكم، الرجاء المحاولة مرة أخرى")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # إضافة المستخدم إلى قاعدة البيانات إذا لم يكن موجوداً
    user_id = update.effective_user.id
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE telegram_id = ?', (user_id,))
    if not c.fetchone():
        c.execute('INSERT INTO users (telegram_id, balance) VALUES (?, ?)', (user_id, 0))
        conn.commit()

    welcome_message = f"""مرحبا بك في نظام تسديد الفواتير
معرف التيليجرام الخاص بك هو: {user_id}
يمكنك استخدام هذا المعرف للتواصل مع الإدارة.
"""
    await update.message.reply_text(welcome_message)

    # جلب الأقسام النشطة من قاعدة البيانات
    c.execute('SELECT name, identifier FROM categories WHERE is_active = 1')
    categories = c.fetchall()
    conn.close()

    # إنشاء أزرار الأقسام
    keyboard = []
    row = []
    for i, category in enumerate(categories):
        row.append(InlineKeyboardButton(category[0], callback_data=f'cat_{category[1]}'))
        if len(row) == 3 or i == len(categories) - 1:
            keyboard.append(row)
            row = []

    # إضافة أزرار الرصيد والطلبات
    keyboard.append([
        InlineKeyboardButton("رصيدي", callback_data='balance'),
        InlineKeyboardButton("طلباتي", callback_data='my_orders')
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('اهلا بك في تسديد الفواتير الرجاء الاختيار علما ان مدة التسديد تتراوح بين 10 والساعتين عدا العطل والضغط يوجد تاخير والدوام من 9ص حتى 9 م', reply_markup=reply_markup)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # التحقق من حالة المستخدم
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT is_active FROM users WHERE telegram_id = ?', (update.effective_user.id,))
    user_status = c.fetchone()
    conn.close()

    # إذا كان المستخدم معطل ويحاول الوصول إلى قسم غير مسموح به
    if user_status and not user_status[0] and query.data.startswith(('cat_', 'buy_')):
        keyboard = [
            [InlineKeyboardButton("رصيدي", callback_data='balance'),
             InlineKeyboardButton("طلباتي", callback_data='my_orders')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("عذراً، حسابك معطل. يمكنك فقط عرض رصيدك وطلباتك.", reply_markup=reply_markup)
        return

    if query.data.startswith('cat_'):
        category = query.data.split('_')[1]
        # جلب اسم القسم من قاعدة البيانات
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT name FROM categories WHERE identifier = ?', (category,))
        category_result = c.fetchone()
        category_name = category_result[0] if category_result else category
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
                f"الشركات المتوفرة في قسم {category_name}:", # Changed from المنتجات to الشركات
                reply_markup=reply_markup
            )
        else:
            keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]] #added back button
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(f"لا توجد شركات متوفرة في قسم {category_name}", reply_markup=reply_markup) # Changed from منتجات to شركات

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

    elif query.data.startswith('cancel_order_'):
        order_id = int(query.data.split('_')[2])
        await query.message.edit_text("الرجاء إدخال سبب الإلغاء:")
        context.user_data['canceling_order_id'] = order_id
        return "WAITING_CANCEL_REASON"

    elif query.data == 'admin_products':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT name, category, is_active FROM products')
        products = c.fetchall()
        conn.close()

        message = "قائمة المنتجات:\n\n"
        for product in products:
            status = "✅ مفعل" if product[2] else "❌ معطل"
            message += f"الاسم: {product[0]}\nالقسم: {product[1]}\nالحالة: {status}\n──────────────\n"

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='admin_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup)

    elif query.data == 'admin_users':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT telegram_id, balance, is_active FROM users')
        users = c.fetchall()
        conn.close()

        message = "قائمة المستخدمين:\n\n"
        for user in users:
            status = "✅ مفعل" if user[2] else "❌ معطل"
            message += f"المعرف: {user[0]}\nالرصيد: {user[1]} ل.س\nالحالة: {status}\n──────────────\n"

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='admin_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup)

    elif query.data == 'admin_orders':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('''SELECT o.id, u.telegram_id, p.name, o.amount, o.status, o.created_at 
                     FROM orders o 
                     JOIN users u ON o.user_id = u.telegram_id 
                     JOIN products p ON o.product_id = p.id 
                     ORDER BY o.created_at DESC LIMIT 10''')
        orders = c.fetchall()
        conn.close()

        message = "آخر 10 طلبات:\n\n"
        for order in orders:
            status = "⏳ قيد المعالجة" if order[4] == "pending" else "✅ مقبول" if order[4] == "accepted" else "❌ مرفوض"
            message += f"رقم الطلب: {order[0]}\nالمستخدم: {order[1]}\nالشركة: {order[2]}\nالمبلغ: {order[3]} ل.س\nالحالة: {status}\nالتاريخ: {order[5]}\n──────────────\n"

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='admin_back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup)

    elif query.data == 'products_menu':
        keyboard = [
            [
                InlineKeyboardButton("إضافة منتج", callback_data='add_product'),
                InlineKeyboardButton("عرض المنتجات", callback_data='view_products')
            ],
            [
                InlineKeyboardButton("تعديل منتج", callback_data='edit_product'),
                InlineKeyboardButton("بحث في المنتجات", callback_data='search_products')
            ],
            [InlineKeyboardButton("رجوع", callback_data='admin_back')]
        ]
        await query.message.edit_text("إدارة المنتجات:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'orders_menu':
        keyboard = [
            [
                InlineKeyboardButton("عرض الطلبات", callback_data='view_orders'),
                InlineKeyboardButton("طلبات معلقة", callback_data='pending_orders'),
                InlineKeyboardButton("إضافة طلب جديد", callback_data='add_new_order')
            ],
            [
                InlineKeyboardButton("بحث في الطلبات", callback_data='search_orders'),
                InlineKeyboardButton("تعديل طلب", callback_data='edit_order')
            ],
            [InlineKeyboardButton("رجوع", callback_data='admin_back')]
        ]
        await query.message.edit_text("إدارة الطلبات:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'users_menu':
        keyboard = [
            [
                InlineKeyboardButton("عرض المستخدمين", callback_data='view_users'),
                InlineKeyboardButton("تعديل مستخدم", callback_data='edit_user')
            ],
            [
                InlineKeyboardButton("بحث في المستخدمين", callback_data='search_users'),
                InlineKeyboardButton("حظر/إلغاء حظر", callback_data='toggle_user')
            ],
            [InlineKeyboardButton("رجوع", callback_data='admin_back')]
        ]
        await query.message.edit_text("إدارة المستخدمين:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'balance_menu':
        keyboard = [
            [
                InlineKeyboardButton("إضافة رصيد", callback_data='add_balance'),
                InlineKeyboardButton("خصم رصيد", callback_data='deduct_balance')
            ],
            [
                InlineKeyboardButton("عرض الأرصدة", callback_data='view_balances'),
                InlineKeyboardButton("تعديل رصيد", callback_data='edit_balance')
            ],
            [InlineKeyboardButton("رجوع", callback_data='admin_back')]
        ]
        await query.message.edit_text("إدارة الأرصدة:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == 'admin_back':
        keyboard = [
            [
                InlineKeyboardButton("المنتجات", callback_data='products_menu'),
                InlineKeyboardButton("الطلبات", callback_data='orders_menu')
            ],
            [
                InlineKeyboardButton("المستخدمين", callback_data='users_menu'),
                InlineKeyboardButton("الأرصدة", callback_data='balance_menu')
            ],
            [InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("مرحباً بك في لوحة التحكم:", reply_markup=reply_markup)

    elif query.data == 'distributor_panel':
        await show_distributor_panel(update, context)
        return

    elif query.data == 'add_user_balance':
        await query.message.edit_text("الرجاء إدخال معرف المستخدم والمبلغ بالصيغة التالية:\nمعرف المستخدم|المبلغ")
        return "WAITING_ADD_USER_BALANCE"

    elif query.data == 'back':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        # التحقق من صلاحية الموزع
        c.execute('SELECT is_distributor FROM users WHERE telegram_id = ?', (update.effective_user.id,))
        is_distributor = c.fetchone()[0] if c.fetchone() else False

        c.execute('SELECT name, identifier FROM categories WHERE is_active = 1')
        categories = c.fetchall()
        conn.close()

        # إنشاء أزرار الأقسام
        keyboard = []
        row = []
        for i, category in enumerate(categories):
            row.append(InlineKeyboardButton(category[0], callback_data=f'cat_{category[1]}'))
            if len(row) == 3 or i == len(categories) - 1:
                keyboard.append(row)
                row = []

        # إضافة أزرار الرصيد والطلبات والموزع
        bottom_row = [
            InlineKeyboardButton("رصيدي", callback_data='balance'),
            InlineKeyboardButton("طلباتي", callback_data='my_orders')
        ]
        if is_distributor:
            bottom_row.append(InlineKeyboardButton("لوحة الموزع", callback_data='distributor_panel'))
        keyboard.append(bottom_row)

        # إنشاء أزرار الأقسام
        keyboard = []
        row = []
        for i, category in enumerate(categories):
            row.append(InlineKeyboardButton(category[0], callback_data=f'cat_{category[1]}'))
            if len(row) == 3 or i == len(categories) - 1:
                keyboard.append(row)
                row = []

        # إضافة أزرار الرصيد والطلبات
        keyboard.append([
            InlineKeyboardButton("رصيدي", callback_data='balance'),
            InlineKeyboardButton("طلباتي", callback_data='my_orders')
        ])

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

    elif query.data == 'view_products':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT name, category, is_active FROM products')
        products = c.fetchall()
        conn.close()

        message = "قائمة المنتجات:\n\n"
        for product in products:
            status = "✅ مفعل" if product[2] else "❌ معطل"
            message += f"الاسم: {product[0]}\nالقسم: {product[1]}\nالحالة: {status}\n──────────────\n"

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='products_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup)

    elif query.data == 'view_orders':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('''SELECT o.id, p.name, o.amount, o.status, o.created_at 
                     FROM orders o 
                     JOIN products p ON o.product_id = p.id 
                     ORDER BY o.created_at DESC LIMIT 10''')
        orders = c.fetchall()
        conn.close()

        message = "آخر 10 طلبات:\n\n"
        for order in orders:
            status = "⏳ قيد المعالجة" if order[3] == "pending" else "✅ مقبول" if order[3] == "accepted" else "❌ مرفوض"
            message += f"رقم الطلب: {order[0]}\nالشركة: {order[1]}\nالمبلغ: {order[2]} ل.س\nالحالة: {status}\nالتاريخ: {order[4]}\n──────────────\n"

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='orders_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup)

    elif query.data == 'view_users':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT telegram_id, balance, is_active FROM users')
        users = c.fetchall()
        conn.close()

        message = "قائمة المستخدمين:\n\n"
        for user in users:
            status = "✅ مفعل" if user[2] else "❌ معطل"
            message += f"المعرف: {user[0]}\nالرصيد: {user[1]} ل.س\nالحالة: {status}\n──────────────\n"

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='users_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup)

    elif query.data == 'view_balances':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT telegram_id, balance FROM users ORDER BY balance DESC')
        balances = c.fetchall()
        conn.close()

        message = "قائمة الأرصدة:\n\n"
        for balance in balances:
            message += f"المعرف: {balance[0]}\nالرصيد: {balance[1]} ل.س\n──────────────\n"

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='balance_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup)

    elif query.data == 'search_users':
        await query.message.edit_text(
            "الرجاء إدخال معرف المستخدم للبحث عنه:"
        )
        return "WAITING_SEARCH_USER"

    elif query.data == 'search_products':
        await query.message.edit_text(
            "الرجاء إدخال اسم المنتج للبحث عنه:"
        )
        return "WAITING_SEARCH_PRODUCT"

    elif query.data == 'search_orders':
        keyboard = [
            [InlineKeyboardButton("البحث برقم الطلب", callback_data='search_by_order_number')],
            [InlineKeyboardButton("البحث ببيانات الزبون", callback_data='search_by_customer_info')],
            [InlineKeyboardButton("رجوع", callback_data='orders_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر طريقة البحث:", reply_markup=reply_markup)

    elif query.data == 'search_by_order_number':
        await query.message.edit_text("الرجاء إدخال رقم الطلب:")
        return "WAITING_SEARCH_ORDER_NUMBER"

    elif query.data == 'search_by_customer_info':
        await query.message.edit_text("الرجاء إدخال بيانات الزبون:")
        return "WAITING_SEARCH_CUSTOMER_INFO"

    elif query.data == 'pending_orders':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('''SELECT o.id, p.name, o.amount, o.customer_info, o.created_at 
                     FROM orders o 
                     JOIN products p ON o.product_id = p.id 
                     WHERE o.status = 'pending'
                     ORDER BY o.created_at DESC''')
        orders = c.fetchall()
        conn.close()

        if not orders:
            message = "لا توجد طلبات معلقة"
        else:
            message = "الطلبات المعلقة:\n\n"
            for order in orders:
                message += f"""رقم الطلب: {order[0]}
الشركة: {order[1]}
المبلغ: {order[2]} ل.س
بيانات الزبون: {order[3]}
التاريخ: {order[4]}
──────────────\n"""

        keyboard = [[InlineKeyboardButton("رجوع", callback_data='orders_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(message, reply_markup=reply_markup)

    elif query.data == 'add_product':
        await query.message.edit_text(
            "الرجاء إدخال معلومات المنتج بالتنسيق التالي:\n"
            "الاسم|القسم\n"
            "مثال: شركة الاتصالات|جوال"
        )
        return "WAITING_NEW_PRODUCT"

    elif query.data == 'edit_product':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT id, name, category FROM products')
        products = c.fetchall()
        conn.close()

        keyboard = []
        for product in products:
            keyboard.append([InlineKeyboardButton(
                f"{product[1]} - {product[2]}", 
                callback_data=f'edit_product_{product[0]}'
            )])
        keyboard.append([InlineKeyboardButton("رجوع", callback_data='products_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر المنتج للتعديل:", reply_markup=reply_markup)

    elif query.data.startswith('edit_product_'):
        product_id = query.data.split('_')[2]
        context.user_data['editing_product'] = product_id
        await query.message.edit_text(
            "الرجاء إدخال المعلومات الجديدة بالتنسيق التالي:\n"
            "الاسم|القسم\n"
            "مثال: شركة الاتصالات|جوال"
        )
        return "WAITING_EDIT_PRODUCT"

    elif query.data == 'add_balance':
        await query.message.edit_text(
            "الرجاء إدخال المعلومات بالتنسيق التالي:\n"
            "معرف المستخدم|المبلغ\n"
            "مثال: 123456789|50000"
        )
        return "WAITING_ADD_BALANCE"

    elif query.data == 'deduct_balance':
        await query.message.edit_text(
            "الرجاء إدخال المعلومات بالتنسيق التالي:\n"
            "معرف المستخدم|المبلغ\n"
            "مثال: 123456789|50000"
        )
        return "WAITING_DEDUCT_BALANCE"

    elif query.data == 'edit_balance':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT telegram_id, balance FROM users')
        users = c.fetchall()
        conn.close()

        keyboard = []
        for user in users:
            keyboard.append([InlineKeyboardButton(
                f"المعرف: {user[0]} - الرصيد: {user[1]}", 
                callback_data=f'edit_balance_{user[0]}'
            )])
        keyboard.append([InlineKeyboardButton("رجوع", callback_data='balance_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر المستخدم لتعديل رصيده:", reply_markup=reply_markup)

    elif query.data.startswith('edit_balance_'):
        user_id = query.data.split('_')[2]
        context.user_data['editing_balance_user'] = user_id
        await query.message.edit_text(
            "الرجاء إدخال المبلغ الجديد"
        )
        return "WAITING_EDIT_BALANCE"


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
    elif query.data == 'add_new_order':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT id, name FROM products WHERE is_active = 1')
        products = c.fetchall()
        conn.close()

        keyboard = []
        for product in products:
            keyboard.append([InlineKeyboardButton(product[1], callback_data=f'add_order_product_{product[0]}')])
        keyboard.append([InlineKeyboardButton("رجوع", callback_data='orders_menu')])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر المنتج للطلب الجديد:", reply_markup=reply_markup)
        return

    elif query.data.startswith('add_order_product_'):
        product_id = query.data.split('_')[3]
        context.user_data['new_order_product_id'] = product_id
        await query.message.edit_text("أدخل معرف المستخدم في تيليجرام:")
        return "WAITING_NEW_ORDER_USER_ID"

    elif query.data == 'edit_order':
        keyboard = [
            [InlineKeyboardButton("البحث برقم الطلب", callback_data='search_order_for_edit')],
            [InlineKeyboardButton("البحث ببيانات الزبون", callback_data='search_customer_for_edit')],
            [InlineKeyboardButton("رجوع", callback_data='orders_menu')]
        ]
        reply_markup =InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر طريقة البحث عن الطلب:", reply_markup=reply_markup)
        return

    elif query.data == 'search_order_for_edit':
        await query.message.edit_text("الرجاء إدخال رقم الطلب:")
        return "WAITING_SEARCH_ORDER_FOR_EDIT"

    elif query.data == 'search_customer_for_edit':
        await query.message.edit_text("الرجاء إدخال بيانات الزبون:")
        return "WAITING_SEARCH_CUSTOMER_FOR_EDIT"

        if not orders:
            keyboard = []
            for order in orders:
                keyboard.append([InlineKeyboardButton(
                    f"طلب #{order[0]} - {order[1]} - {order[2]} ل.س",
                    callback_data=f'edit_order_{order[0]}'
                )])
            keyboard.append([InlineKeyboardButton("رجوع", callback_data='orders_menu')])

            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text("اختر الطلب للتعديل:", reply_markup=reply_markup)
            return

    elif query.data.startswith('edit_order_'):
        order_id = query.data.split('_')[2]
        context.user_data['editing_order_id'] = order_id

        keyboard = [
            [
                InlineKeyboardButton("تعديل المبلغ", callback_data=f'edit_order_amount_{order_id}'),
                InlineKeyboardButton("تعديل الحالة", callback_data=f'edit_order_status_{order_id}')
            ],
            [InlineKeyboardButton("رجوع", callback_data='orders_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر ما تريد تعديله:", reply_markup=reply_markup)
        return

    elif query.data.startswith('edit_order_amount_'):
        order_id = query.data.split('_')[3]
        context.user_data['editing_order_id'] = order_id
        await query.message.edit_text("أدخل المبلغ الجديد:")
        return "WAITING_EDIT_ORDER_AMOUNT"

    elif query.data.startswith('edit_order_status_'):
        order_id = query.data.split('_')[3]
        context.user_data['editing_order_id'] = order_id
        keyboard = [
            [InlineKeyboardButton("قيد المعالجة", callback_data=f'set_order_status_pending_{order_id}')],
            [InlineKeyboardButton("مقبول", callback_data=f'set_order_status_accepted_{order_id}')],
            [InlineKeyboardButton("مرفوض", callback_data=f'set_order_status_rejected_{order_id}')],
            [InlineKeyboardButton("رجوع", callback_data='orders_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر الحالة الجديدة:", reply_markup=reply_markup)
        return

    elif query.data.startswith('set_order_status_'):
        order_id = query.data.split('_')[4]
        status = query.data.split('_')[3]
        await update_order_status(update, context, order_id, status)
        return

    elif query.data.startswith('search_order_number'):
        await query.message.edit_text("الرجاء إدخال رقم الطلب:")
        return "WAITING_SEARCH_ORDER_NUMBER"

    elif query.data.startswith('search_customer_info'):
        await query.message.edit_text("الرجاء إدخال بيانات الزبون:")
        return "WAITING_SEARCH_CUSTOMER_INFO"


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


async def handle_search_order_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_number = update.message.text
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info 
                 FROM orders o 
                 JOIN products p ON o.product_id = p.id 
                 WHERE o.id = ?''', (order_number,))
    order = c.fetchone()
    conn.close()

    if order:
        keyboard = [
            [InlineKeyboardButton("تعديل المبلغ", callback_data=f'edit_order_amount_{order[0]}')],
            [InlineKeyboardButton("تعديل الحالة", callback_data=f'edit_order_status_{order[0]}')],
            [InlineKeyboardButton("رجوع", callback_data='orders_menu')]
        ]
        message = f"""
رقم الطلب: {order[0]}
الشركة: {order[1]}
المبلغ: {order[2]} ل.س
الحالة: {order[3]}
بيانات الزبون: {order[4]}
"""
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup)
    else:
        await update.message.reply_text("لم يتم العثور على الطلب")
    return ConversationHandler.END

async def handle_search_customer_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    customer_info = update.message.text
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info 
                 FROM orders o 
                 JOIN products p ON o.product_id = p.id 
                 WHERE o.customer_info LIKE ?''', ('%' + customer_info + '%',))
    orders = c.fetchall()
    conn.close()

    if orders:
        keyboard = []
        for order in orders:
            keyboard.append([InlineKeyboardButton(
                f"طلب #{order[0]} - {order[1]} - {order[2]} ل.س",
                callback_data=f'edit_order_{order[0]}'
            )])
        keyboard.append([InlineKeyboardButton("رجوع", callback_data='orders_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("اختر الطلب للتعديل:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("لم يتم العثور على طلبات مطابقة")
    return ConversationHandler.END

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
                     WHERE o.customer_info LIKE ? AND o.user_id = ?''', ('%' + customer_info + '%', update.effectiveuser.id))
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

async def handle_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_query = update.message.text
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT telegram_id, balance, is_active FROM users WHERE telegram_id LIKE ?', ('%' + user_query + '%',))
    users = c.fetchall()
    conn.close()

    if users:
        message = "نتائج البحث:\n\n"
        for user in users:
            status = "✅ مفعل" if user[2] else "❌ معطل"
            message += f"المعرف: {user[0]}\nالرصيد: {user[1]} ل.س\nالحالة: {status}\n──────────────\n"
    else:
        message = "لم يتم العثور على مستخدمين"

    keyboard = [[InlineKeyboardButton("رجوع", callback_data='users_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)
    return ConversationHandler.END

async def handle_search_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    product_query = update.message.text
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT name, category, is_active FROM products WHERE name LIKE ?', ('%' + product_query + '%',))
    products = c.fetchall()
    conn.close()

    if products:
        message = "نتائج البحث:\n\n"
        for product in products:
            status = "✅ مفعل" if product[2] else "❌ معطل"
            message += f"الاسم: {product[0]}\nالقسم: {product[1]}\nالحالة: {status}\n──────────────\n"
    else:
        message = "لم يتم العثور على منتجات"

    keyboard = [[InlineKeyboardButton("رجوع", callback_data='products_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)
    return ConversationHandler.END

async def handle_new_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        name, category = update.message.text.split('|')
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('INSERT INTO products (name, category, is_active) VALUES (?, ?, 1)', (name.strip(), category.strip()))
        conn.commit()
        conn.close()
        message = "تم إضافة المنتج بنجاح"
    except ValueError:
        message = "صيغة غير صحيحة. الرجاء استخدام: الاسم|القسم"
    except Exception as e:
        message = f"حدث خطأ: {str(e)}"

    keyboard = [[InlineKeyboardButton("رجوع", callback_data='products_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)
    return ConversationHandler.END

async def handle_edit_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        name, category = update.message.text.split('|')
        product_id = context.user_data.get('editing_product')

        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('UPDATE products SET name = ?, category = ? WHERE id = ?', 
                 (name.strip(), category.strip(), product_id))
        conn.commit()
        conn.close()
        message = "تم تعديل المنتج بنجاح"
    except ValueError:
        message = "صيغة غير صحيحة. الرجاء استخدام: الاسم|القسم"
    except Exception as e:
        message = f"حدث خطأ: {str(e)}"

    keyboard = [[InlineKeyboardButton("رجوع", callback_data='products_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)
    return ConversationHandler.END

async def handle_add_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id, amount = update.message.text.split('|')
        amount = float(amount.strip())

        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?', (amount, user_id.strip()))
        conn.commit()
        conn.close()
        message = f"تم إضافة {amount} ل.س للمستخدم {user_id}"
    except ValueError:
        message = "صيغة غير صحيحة. الرجاء استخدام: معرف المستخدم|المبلغ"
    except Exception as e:
        message = f"حدث خطأ: {str(e)}"

    keyboard = [[InlineKeyboardButton("رجوع", callback_data='balance_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)
    return ConversationHandler.END

async def handle_deduct_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id, amount = update.message.text.split('|')
        amount = float(amount.strip())

        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?', (amount, user_id.strip()))
        conn.commit()
        conn.close()
        message = f"تم خصم {amount} ل.س من المستخدم {user_id}"
    except ValueError:
        message = "صيغة غير صحيحة. الرجاء استخدام: معرف المستخدم|المبلغ"
    except Exception as e:
        message = f"حدث خطأ: {str(e)}"

    keyboard = [[InlineKeyboardButton("رجوع", callback_data='balance_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)
    return ConversationHandler.END

async def handle_edit_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_balance = float(update.message.text)
        user_id = context.user_data.get('editing_balance_user')

        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('UPDATE users SET balance = ? WHERE telegram_id = ?', (new_balance, user_id))
        conn.commit()
        conn.close()
        message = f"تم تعديل رصيد المستخدم {user_id} إلى {new_balance} ل.س"
    except ValueError:
        message = "الرجاء إدخال رقم صحيح"
    except Exception as e:
        message = f"حدث خطأ: {str(e)}"

    keyboard = [[InlineKeyboardButton("رجوع", callback_data='balance_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)
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

async def handle_new_order_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.text
    try:
        user_id = int(user_id)
        context.user_data['new_order_user_id'] = user_id
        await update.message.reply_text("أدخل المبلغ:")
        return "WAITING_NEW_ORDER_AMOUNT"
    except ValueError:
        await update.message.reply_text("معرف مستخدم غير صحيح، الرجاء المحاولة مرة أخرى.")
        return ConversationHandler.END

async def handle_new_order_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = update.message.text
    try:
        amount = float(amount)
        context.user_data['new_order_amount'] = amount
        await create_new_order(update, context)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("مبلغ غير صحيح، الرجاء المحاولة مرة أخرى.")
        return ConversationHandler.END

async def create_new_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.get('new_order_user_id')
    product_id = context.user_data.get('new_order_product_id')
    amount = context.user_data.get('new_order_amount')
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT balance FROM users WHERE telegram_id = ?', (user_id,))
    user_balance = c.fetchone()[0]
    if user_balance < amount:
        await update.message.reply_text(f"رصيد المستخدم غير كافٍ، رصيده الحالي {user_balance}")
        conn.close()
        return
    c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?', (amount, user_id))
    c.execute('INSERT INTO orders (user_id, product_id, amount, status) VALUES (?, ?, ?, ?)', (user_id, product_id, amount, 'pending'))
    order_id = c.lastrowid
    conn.commit()
    conn.close()
    await update.message.reply_text(f"تم إضافة طلب جديد برقم {order_id}")


async def handle_edit_order_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_amount = update.message.text
    try:
        new_amount = float(new_amount)
        await update_order_amount(update, context, new_amount)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("مبلغ غير صحيح، الرجاء المحاولة مرة أخرى.")
        return ConversationHandler.END

async def update_order_amount(update: Update, context: ContextTypes.DEFAULT_TYPE, new_amount):
    order_id = context.user_data.get('editing_order_id')
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT amount, user_id, status FROM orders WHERE id = ?', (order_id,))
    current_order = c.fetchone()
    if not current_order:
        await update.message.reply_text("الطلب غير موجود")
        conn.close()
        return
    current_amount = current_order[0]
    user_id = current_order[1]
    status = current_order[2]
    if status != 'rejected':
        amount_diff = new_amount - current_amount
        if amount_diff > 0:
            c.execute('SELECT balance FROM users WHERE telegram_id = ?', (user_id,))
            user_balance = c.fetchone()[0]
            if user_balance < amount_diff:
                await update.message.reply_text(f"رصيد المستخدم غير كافٍ، رصيده الحالي {user_balance}")
                conn.close()
                return
            c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?', (amount_diff, user_id))
        elif amount_diff < 0:
            c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?', (-amount_diff, user_id))
    c.execute('UPDATE orders SET amount = ? WHERE id = ?', (new_amount, order_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"تم تحديث مبلغ الطلب {order_id} إلى {new_amount}")

async def update_order_status(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id, status):
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('SELECT amount, user_id, status FROM orders WHERE id = ?', (order_id,))
    current_order = c.fetchone()
    if not current_order:
        await update.message.reply_text("الطلب غير موجود")
        conn.close()
        return
    amount = current_order[0]
    user_id = current_order[1]
    current_status = current_order[2]
    if current_status == 'rejected' and (status == 'pending' or status == 'accepted'):
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', (user_id,))
        user_balance = c.fetchone()[0]
        if user_balance < amount:
            await update.message.reply_text(f"رصيد المستخدم غير كافٍ، رصيده الحالي {user_balance}")
            conn.close()
            return
        c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?', (amount, user_id))
    elif current_status != 'rejected' and status == 'rejected':
        c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?', (amount, user_id))
    c.execute('UPDATE orders SET status = ? WHERE id = ?', (status, order_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"تم تحديث حالة الطلب {order_id} إلى {status}")


# Flask routes
@app.route('/add_category', methods=['POST'])
def add_category():
    name = request.form['name']
    identifier = request.form['identifier']
    is_active = 'is_active' in request.form
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('INSERT INTO categories (name, identifier, is_active) VALUES (?, ?, ?)',
              (name, identifier, is_active))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/toggle_category', methods=['POST'])
def toggle_category():
    category_id = request.form['category_id']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()

    # تحديث حالة القسم
    c.execute('UPDATE categories SET is_active = NOT is_active WHERE id = ?', (category_id,))

    # جلب معلومات القسم المحدث
    c.execute('SELECT identifier, is_active FROM categories WHERE id = ?', (category_id,))
    category = c.fetchone()

    if category:
        # تحديث المنتجات المرتبطة بالقسم
        c.execute('UPDATE products SET is_active = ? WHERE category = ?', 
                 (category[1], category[0]))

    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))

@app.route('/edit_category', methods=['POST'])
def edit_category():
    category_id = request.form['category_id']
    name = request.form['name']
    identifier = request.form['identifier']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE categories SET name = ?, identifier = ? WHERE id = ?',
              (name, identifier, category_id))
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

@app.route('/')
def admin_panel():
    try:
        conn = sqlite3.connect('store.db')
        c = conn.cursor()

        # إضافة جدول الأقسام إذا لم يكن موجوداً
        c.execute('''CREATE TABLE IF NOT EXISTS categories
                     (id INTEGER PRIMARY KEY, name TEXT, identifier TEXT, is_active BOOLEAN DEFAULT 1)''')

        # إضافة الأقسام الافتراضية إذا كان الجدول فارغاً
        c.execute('SELECT COUNT(*) FROM categories')
        if c.fetchone()[0] == 0:
            default_categories = [
                ('إنترنت', 'internet', 1),
                ('جوال', 'mobile', 1),
                ('خط أرضي', 'landline', 1),
                ('البنوك', 'banks', 1)
            ]
            c.executemany('INSERT INTO categories (name, identifier, is_active) VALUES (?, ?, ?)',
                         default_categories)
            conn.commit()

        # التأكد من وجود الجداول
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

        c.execute('SELECT * FROM categories')
        categories = c.fetchall()
        c.execute('SELECT * FROM products')
        products = c.fetchall()
        c.execute('SELECT * FROM users')
        users = c.fetchall()
        c.execute('SELECT telegram_id FROM users WHERE id = 1')
        admin_id = c.fetchone()

        if admin_id and admin_id[0]:
            c.execute('''SELECT o.id, o.user_id, p.name, o.amount, o.customer_info, o.status, o.created_at, o.note
                         FROM orders o 
                         JOIN products p ON o.product_id = p.id 
                         ORDER BY o.created_at DESC''')
        else:
            user_telegram_id = session.get('user_telegram_id')
            c.execute('''SELECT o.id, o.user_id, p.name, o.amount, o.customer_info, o.status, o.created_at, o.note
                         FROM orders o 
                         JOIN products p ON o.product_id = p.id 
                         WHERE o.user_id = ?
                         ORDER BY o.created_at DESC''', (user_telegram_id,))
        orders = c.fetchall()
        conn.close()
        return render_template('admin.html', categories=categories, products=products, users=users, orders=orders)
    except Exception as e:
        print(f"Error in admin_panel: {str(e)}")
        if conn:
            conn.close()
        return "حدث خطأ في الوصول إلى لوحة التحكم. الرجاء المحاولة مرة أخرى.", 500

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
    try:
        product_id = request.form['product_id']
        conn = sqlite3.connect('store.db')  # تصحيح اسم قاعدة البيانات
        c = conn.cursor()

        # التحقق من وجود الجدول
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='products'")
        if not c.fetchone():
            c.execute('''CREATE TABLE IF NOT EXISTS products 
                     (id INTEGER PRIMARY KEY, name TEXT, category TEXT, is_active BOOLEAN DEFAULT 1)''')
            conn.commit()

        c.execute('DELETE FROM products WHERE id = ?', (product_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in delete_product: {str(e)}")
        return "حدث خطأ في حذف المنتج", 500

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

@app.route('/add_order', methods=['POST'])
def add_order():
    try:
        user_id = int(request.form['user_id'])
        product_id = int(request.form['product_id'])
        amount = float(request.form['amount'])
        customer_info = request.form['customer_info']

        conn = sqlite3.connect('store.db')
        c = conn.cursor()

        # التحقق من وجود المستخدم والمنتج
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', (user_id,))
        user = c.fetchone()

        if not user:
            conn.close()
            return "المستخدم غير موجود", 400

        if user[0] < amount:
            conn.close()
            return "رصيد المستخدم غير كافي", 400

        # خصم المبلغ من رصيد المستخدم
        c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?',
                 (amount, user_id))

        # إنشاء الطلب
        c.execute('''INSERT INTO orders (user_id, product_id, amount, customer_info, status) 
                     VALUES (?, ?, ?, ?, ?)''',
                 (user_id, product_id, amount, customer_info, 'pending'))

        order_id = c.lastrowid

        # الحصول على اسم المنتج للإشعار
        c.execute('SELECT name FROM products WHERE id = ?', (product_id,))
        product_name = c.fetchone()[0]

        conn.commit()
        conn.close()

        # إرسال إشعار للمستخدم
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        bot = telegram.Bot(token=bot_token)

        notification_message = f"""✉️ تم إنشاء طلب جديد
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ: {amount} ليرة سوري
بيانات الزبون: {customer_info}"""

        try:
            asyncio.run(bot.send_message(chat_id=user_id, text=notification_message))
        except Exception as e:
            print(f"خطأ في إرسال الإشعار: {str(e)}")

        return redirect(url_for('admin_panel'))

    except Exception as e:
        print(f"Error in add_order: {str(e)}")
        return f"حدث خطأ في إضافة الطلب: {str(e)}", 500

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

@app.route('/toggle_distributor', methods=['POST'])
def toggle_distributor():
    try:
        user_id = request.form['user_id']
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('''UPDATE users SET is_distributor = 
                     CASE WHEN is_distributor = 1 THEN 0 ELSE 1 END 
                     WHERE telegram_id = ?''', (user_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in toggle_distributor: {str(e)}")
        return "حدث خطأ في تغيير صلاحية الموزع", 500

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

# تم إزالة وظيفة حذف الطلبات لمنع حذف أي طلب

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات المحلية فقط"""
    try:
        conn = sqlite3.connect('store.db', timeout=20)
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA timezone = '+03:00'")
        return conn

        # إذا لم تكن موجودة، قم بإنشاء قاعدة بيانات جديدة
        conn = sqlite3.connect('store.db')
        conn.execute("PRAGMA timezone = '+03:00'")

        # إنشاء الجداول الأساسية
        c = conn.cursor()
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
        return conn
    except Exception as e:
        print(f"خطأ في مزامنة قاعدة البيانات: {str(e)}")
        return sqlite3.connect('store.db')

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False)

def run_bot():
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
            CallbackQueryHandler(button_click, pattern="^(?!cancel$).*$")
        ],
        states={
            "WAITING_SEARCH_ORDER_NUMBER": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_order_number),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_CUSTOMER_INFO": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_customer_info),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_AMOUNT": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_CONFIRMATION": [
                CallbackQueryHandler(handle_purchase_confirmation)
            ],
            "WAITING_ORDER_NUMBER": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_order_number),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_SEARCH_CUSTOMER_INFO": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_customer_info),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_CANCEL_REASON": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cancel_reason),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_SEARCH_USER": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_user),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_SEARCH_PRODUCT": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_product),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_NEW_PRODUCT": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_product),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_EDIT_PRODUCT": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_product),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_ADD_BALANCE": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_balance),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_DEDUCT_BALANCE": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_deduct_balance),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_EDIT_BALANCE": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_balance),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_NEW_ORDER_USER_ID": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_order_user_id),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_NEW_ORDER_AMOUNT": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_order_amount),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_EDIT_ORDER_AMOUNT": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_order_amount),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_SEARCH_ORDER_FOR_EDIT": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_order_for_edit),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_SEARCH_CUSTOMER_FOR_EDIT": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_customer_for_edit),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_ADD_USER_BALANCE": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_user_balance),
                CallbackQueryHandler(button_click, pattern="^back$")
            ]

        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            CallbackQueryHandler(button_click, pattern="^cancel$")
        ],
        per_message=False,
        per_chat=True
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

    # تهيئة قاعدة البيانات
    init_db()

    # التحقق من عدم وجود نسخة أخرى من البوت
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        # استخدام ملف قفل لمنع تشغيل نسخ متعددة
        if os.path.exists('bot.lock'):
            try:
                with open('bot.lock', 'r') as f:
                    pid = int(f.read().strip())
                try:
                    os.kill(pid, 0)  # اختبار إذا كانت العملية نشطة
                    print("هناك نسخة أخرى من البوت قيد التشغيل")
                    exit(1)
                except OSError:
                    pass  # العملية غير موجودة
            except:
                pass

        # تسجيل PID العملية الحالية
        with open('bot.lock', 'w') as f:
            f.write(str(os.getpid()))

        sock.bind(('0.0.0.0', 5001))  # منفذ للتحقق فقط

        # تشغيل التطبيق
        app.config['TEMPLATES_AUTO_RELOAD'] = True
        app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # مدة الجلسة يوم كامل
        flask_thread = Thread(target=run_flask)
        flask_thread.start()
        run_bot()

    except socket.error:
        print("هناك نسخة أخرى من البوت قيد التشغيل")
        exit(1)
    except Exception as e:
        print(f"خطأ: {str(e)}")
        if os.path.exists('bot.lock'):
            os.remove('bot.lock')
        exit(1)
    finally:
        sock.close()
        if os.path.exists('bot.lock'):
            os.remove('bot.lock')
async def show_distributor_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("إضافة رصيد لمستخدم", callback_data='add_user_balance')],
        [InlineKeyboardButton("رجوع", callback_data='back')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.message.edit_text(
        "مرحباً بك في لوحة الموزع\nالرجاء اختيار العملية المطلوبة:",
        reply_markup=reply_markup
    )

async def handle_add_user_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id, amount = update.message.text.split('|')
        user_id = int(user_id.strip())
        amount = float(amount.strip())

        conn = sqlite3.connect('store.db')
        c = conn.cursor()

        # التحقق من رصيد الموزع
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', (update.effective_user.id,))
        distributor_balance = c.fetchone()[0]

        if distributor_balance < amount:
            await update.message.reply_text("عذراً، رصيدك غير كافي")
            conn.close()
            return ConversationHandler.END

        # خصم المبلغ من الموزع وإضافته للمستخدم
        c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?',
                 (amount, update.effective_user.id))
        c.execute('UPDATE users SET balance = balance + ? WHERE telegram_id = ?',
                 (amount, user_id))
        conn.commit()

        # إرسال إشعار للمستخدم
        await context.bot.send_message(
            chat_id=user_id,
            text=f"تم إضافة {amount} ليرة سوري إلى رصيدك من قبل الموزع"
        )

        conn.close()
        await update.message.reply_text("تمت العملية بنجاح")
    except:
        await update.message.reply_text("حدث خطأ. الرجاء التأكد من صحة المعلومات المدخلة")
    return ConversationHandler.END

@app.route('/toggle_distributor', methods=['POST'])
def toggle_distributor():
    user_id = request.form['user_id']
    conn = sqlite3.connect('store.db')
    c = conn.cursor()
    c.execute('UPDATE users SET is_distributor = NOT is_distributor WHERE telegram_id = ?',
              (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('admin_panel'))