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
                 (id INTEGER PRIMARY KEY, name TEXT, category TEXT, is_active BOOLEAN DEFAULT 1,
                  enable_speeds BOOLEAN DEFAULT 0,
                  enable_packages BOOLEAN DEFAULT 0,
                  enable_custom_amount BOOLEAN DEFAULT 1)''')

    c.execute('''CREATE TABLE IF NOT EXISTS speeds
                 (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT, price REAL, is_active BOOLEAN DEFAULT 1,
                 FOREIGN KEY(product_id) REFERENCES products(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS packages
                 (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT, price REAL, is_active BOOLEAN DEFAULT 1,
                 FOREIGN KEY(product_id) REFERENCES products(id))''')

    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY, telegram_id INTEGER, balance REAL, 
                  phone_number TEXT, is_active BOOLEAN DEFAULT 1, note TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id INTEGER PRIMARY KEY, user_id INTEGER, product_id INTEGER, amount REAL, 
                  customer_info TEXT, status TEXT DEFAULT 'pending', rejection_note TEXT,
                  created_at TIMESTAMP DEFAULT (datetime('now', '+3 hours')), note TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS categories
                     (id INTEGER PRIMARY KEY, name TEXT, identifier TEXT, is_active BOOLEAN DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS megas
                 (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT, price REAL, is_active BOOLEAN DEFAULT 1,
                 FOREIGN KEY(product_id) REFERENCES products(id))''')

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
    keyboard.append([
        InlineKeyboardButton("التواصل مع الدعم الفني", url='https://t.me/nourrod')
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
        await query.message.edit_text("عذراً، الخدمة متوقفة مؤقتاً حاول في وقت اخر. كما يمكنك في الوقت الحالي فقط مشاهدة رصيدك وطلباتك.", reply_markup=reply_markup)
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
                InlineKeyboardButton("طلبات معلقة", callback_data='pending_orders')
            ],
            [
                InlineKeyboardButton("طلب جديد", callback_data='add_new_order'),
                InlineKeyboardButton("تعديل طلب", callback_data='edit_order')
            ],
            [
                InlineKeyboardButton("بحث في الطلبات", callback_data='search_orders')
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

        # جلب الأقسام النشطة
        c.execute('SELECT name, identifier FROM categories WHERE is_active = 1')
        categories = c.fetchall()

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

        conn.close()
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text('اهلا بك في تسديد الفواتير الرجاء الاختيار علما ان مدة التسديد تتراوح بين 10 والساعتين عدا العطل والضغط يوجد تاخير والدوام من 9ص حتى 9 م', reply_markup=reply_markup)

    elif query.data == 'balance':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', (update.effective_user.id,))
        result = c.fetchone()
        balance = result[0] if result else 0
        conn.close()
        keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(f"رصيدك الحالي: {balance} ليرة سوري", reply_markup=reply_markup)
        return

    elif query.data == 'add_balance':
        await query.message.edit_text("الرجاء إدخال معرف المستخدم والمبلغ بالصيغة التالية:\nمعرف المستخدم|المبلغ")
        return "WAITING_ADD_BALANCE"

    elif query.data == 'back_to_main':
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        try:
            c.execute('SELECT category FROM products WHERE id = ?', (context.user_data.get('product_id'),))
            category = c.fetchone()

            if category:
                c.execute('SELECT * FROM products WHERE category = ? AND is_active = 1', (category[0],))
                products = c.fetchall()
                keyboard = []
                for product in products:
                    keyboard.append([InlineKeyboardButton(f"{product[1]}", callback_data=f'buy_{product[0]}')])
                keyboard.append([InlineKeyboardButton("رجوع", callback_data='back')])
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.edit_text(f"الشركات المتوفرة في قسم {category[0]}:", reply_markup=reply_markup)
            else:
                keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.edit_text("اختر من القائمة:", reply_markup=reply_markup)
        except Exception as e:
            print(f"Error in back_to_main: {str(e)}")
            keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text("حدث خطأ، الرجاء المحاولة مرة أخرى", reply_markup=reply_markup)
        finally:
            conn.close()
        return

    elif query.data.startswith('buy_'):
        product_id = int(query.data.split('_')[1])
        context.user_data['product_id'] = product_id
        context.user_data['last_menu'] = 'main_menu'  # Save the last menu state

        conn = sqlite3.connect('store.db')
        c = conn.cursor()

        try:
            # التحقق من وجود باقات أو سرعات للمنتج
            c.execute('SELECT name FROM products WHERE id = ?', (product_id,))
            product = c.fetchone()

            c.execute('SELECT COUNT(*) FROM megas WHERE product_id = ? AND is_active = 1', (product_id,))
            has_megas = c.fetchone()[0] > 0

            c.execute('SELECT COUNT(*) FROM speeds WHERE product_id = ? AND is_active = 1', (product_id,))
            has_speeds = c.fetchone()[0] > 0

            keyboard = []
            keyboard.append([InlineKeyboardButton("إضافة دفعة", callback_data=f'manual_balance_{product_id}')])

            row = []
            if has_megas:
                row.append(InlineKeyboardButton("الباقات", callback_data=f'megas_{product_id}'))

            if has_speeds:
                row.append(InlineKeyboardButton("السرعات", callback_data=f'speeds_{product_id}'))

            if row:
                keyboard.append(row)

            keyboard.append([InlineKeyboardButton("رجوع للقائمة السابقة", callback_data='back_to_main')])

            context.user_data['product_name'] = product[0]

            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(f"اختر نوع الخدمة لـ {product[0]}:", reply_markup=reply_markup)
        finally:
            conn.close()
        return

    elif query.data.startswith('megas_'):
        product_id = int(query.data.split('_')[1])
        context.user_data['last_menu'] = 'product_menu'  # Save the last menu state
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT id, name, price FROM megas WHERE product_id = ? AND is_active = 1', (product_id,))
        megas = c.fetchall()
        conn.close()

        keyboard = []
        for mega in megas:
            keyboard.append([InlineKeyboardButton(
                f"{mega[1]} - {mega[2]} ل.س",
                callback_data=f'select_mega_{mega[0]}_{product_id}'
            )])
        keyboard.append([InlineKeyboardButton("رجوع للمنتج", callback_data=f'buy_{product_id}')])
        keyboard.append([InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر الباقة المناسبة:", reply_markup=reply_markup)
        return

    elif query.data.startswith('speeds_'):
        product_id = int(query.data.split('_')[1])
        context.user_data['last_menu'] = 'product_menu'  # Save the last menu state
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT id, name, price FROM speeds WHERE product_id = ? AND is_active = 1', (product_id,))
        speeds = c.fetchall()
        conn.close()

        keyboard = []
        for speed in speeds:
            keyboard.append([InlineKeyboardButton(
                f"{speed[1]} - {speed[2]} ل.س",
                callback_data=f'select_speed_{speed[0]}_{product_id}'
            )])
        keyboard.append([InlineKeyboardButton("رجوع للمنتج", callback_data=f'buy_{product_id}')])
        keyboard.append([InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("اختر السرعة المناسبة:", reply_markup=reply_markup)
        return

    elif query.data.startswith('select_mega_') or query.data.startswith('select_speed_'):
        try:
            parts = query.data.split('_')
            item_type = parts[1]  # mega or speed
            item_id = int(parts[2])
            product_id = int(parts[3])

            conn = sqlite3.connect('store.db')
            c = conn.cursor()

            table_name = 'megas' if item_type == 'mega' else 'speeds'
            c.execute(f'SELECT name, price FROM {table_name} WHERE id = ?', (item_id,))
            item = c.fetchone()

            if not item:
                await query.message.edit_text("حدث خطأ، الرجاء المحاولة مرة أخرى")
                return

            context.user_data['product_id'] = product_id
            context.user_data['amount'] = item[1]  # السعر
            context.user_data['customer_info'] = None  # تهيئة بيانات الزبون

            # حفظ معرف الباقة أو السرعة المختارة
            if item_type == 'mega':
                context.user_data['selected_mega'] = item_id
                context.user_data['selected_speed'] = None
            else:
                context.user_data['selected_speed'] = item_id
                context.user_data['selected_mega'] = None

            # التحقق من الرصيد
            c.execute('SELECT balance FROM users WHERE telegram_id = ?', (update.effective_user.id,))
            user_balance = c.fetchone()[0]

            if user_balance < item[1]:
                await query.message.edit_text(f"عذراً، رصيدك غير كافي. رصيدك الحالي: {user_balance} ليرة سوري")
                return

            # عرض تأكيد الطلب مباشرة مع تفاصيل السعر
            confirmation_message = f"""
سيتم خصم {item[1]} ليرة سوري من رصيدك مقابل {item[0]}.
الرجاء إدخال بيانات الزبون:"""

            await query.message.edit_text(confirmation_message)
            return "WAITING_CUSTOMER_INFO"

        except Exception as e:
            print(f"Error in select_mega_speed: {str(e)}")
            await query.message.edit_text("حدث خطأ، الرجاء المحاولة مرة أخرى")
            return
        finally:
            if 'conn' in locals():
                c.close()
                conn.close()

    elif query.data.startswith('manual_balance_'):
        product_id = int(query.data.split('_')[2])
        context.user_data['product_id'] = product_id
        # مسح المبلغ السابق إن وجد
        if 'amount' in context.user_data:
            del context.user_data['amount']

        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        try:
            c.execute('SELECT name FROM products WHERE id = ?', (product_id,))
            product_name = c.fetchone()[0]
            context.user_data['product_name'] = product_name
            await query.message.edit_text("الرجاء إدخال بيانات الزبون:")
        finally:
            conn.close()
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

    # إذا كان المبلغ محدد مسبقاً (من اختيار باقة أو سرعة)
    if 'amount' in context.user_data:
        amount = context.user_data['amount']
        await update.message.reply_text(
            f"سيتم خصم {amount} ليرة سوري من رصيدك.\n"
            f"اضغط على تأكيد لإتمام العملية.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("تأكيد", callback_data='confirm_purchase'),
                InlineKeyboardButton("إلغاء", callback_data='cancel_purchase')
            ]])
        )
        return "WAITING_CONFIRMATION"
    else:
        # في حالة الإدخال اليدوي للمبلغ
        await update.message.reply_text("الرجاء إدخال المبلغ:")
        return "WAITING_AMOUNT"

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        if 'product_id' not in context.user_data:
            keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("حدث خطأ في معرف المنتج، الرجاء بدء العملية من جديد", reply_markup=reply_markup)
            return ConversationHandler.END

        context.user_data['amount'] = amount

        # التحقق من الرصيد
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', (update.effective_user.id,))
        user_balance = c.fetchone()[0]

        if amount > user_balance:
            await update.message.reply_text(f"عذراً، رصيدك غير كافي. رصيدك الحالي: {user_balance} ليرة سوري")
            conn.close()
            return ConversationHandler.END

        c.execute('SELECT name FROM products WHERE id = ?', (context.user_data['product_id'],))
        product = c.fetchone()
        conn.close()

        if not product:
            keyboard = [[InlineKeyboardButton("رجوع للقائمة الرئيسية", callback_data='back')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("المنتج غير متوفر، الرجاء بدء العملية من جديد", reply_markup=reply_markup)
            return ConversationHandler.END

        product_name = product[0]

        conn = sqlite3.connect('store.db')
        c = conn.cursor()

        try:
            c.execute('SELECT balance FROM users WHERE telegram_id = ?', (update.effective_user.id,))
            user_balance = c.fetchone()

            if not user_balance or user_balance[0] < amount:
                await update.message.reply_text(f"عذراً، رصيدك غير كافي. رصيدك الحالي: {user_balance[0] if user_balance else 0} ليرة سوري")
                conn.close()
                return ConversationHandler.END

            # التحقق من وجود المنتج
            c.execute('SELECT name FROM products WHERE id = ?', (context.user_data['product_id'],))
            product = c.fetchone()

            if not product:
                await update.message.reply_text("المنتج غير متوفر")
                conn.close()
                return ConversationHandler.END

            product_name = product[0]
        finally:
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
    except ValueError:
        await update.message.reply_text("الرجاء إدخال رقم صحيح")
        return "WAITING_AMOUNT"
    except Exception as e:
        print(f"Error in handle_amount: {str(e)}")
        await update.message.reply_text("حدث خطأ، الرجاء المحاولة مرة أخرى")
        return ConversationHandler.END


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
        await query.message.edit_text("اختر الطلب للتعديل:", reply_markup=reply_markup)
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
                c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info, o.created_at, o.rejection_note, u.telegram_id
                            FROM orders o 
                            JOIN products p ON o.product_id = p.id 
                            JOIN users u ON o.user_id = u.telegram_id
                            WHERE o.id = ?''', (order_number,))
            else:
                # المستخدم العادي يبحث في طلباته فقط
                c.execute('''SELECT o.id, p.name, o.amount, o.status, o.customer_info, o.created_at, o.rejection_note, u.telegram_id
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

                if order[3] == "rejected" and order[6]:  # إضافة سبب الرفض
                    message += f"\nسبب الرفض: {order[6]}"

                # إضافة معرف التيليجرام فقط للمدير
                if is_admin:
                    message += f"\nمعرف التيليجرام لمقدم الطلب: {order[7]}"


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

    # تحديد نوع الخدمة
    service_type = None
    if 'selected_speed' in context.user_data and context.user_data['selected_speed']:
        service_type = f"speed_{context.user_data['selected_speed']}"
    elif 'selected_mega' in context.user_data and context.user_data['selected_mega']:
        service_type = f"mega_{context.user_data['selected_mega']}"

    # خصم المبلغ من رصيد المستخدم وإنشاء الطلب
    c.execute('UPDATE users SET balance = balance - ? WHERE telegram_id = ?',
              (amount, update.effective_user.id))
    c.execute('INSERT INTO orders (user_id, product_id, amount, customer_info, note) VALUES (?, ?, ?, ?, ?)',
              (update.effective_user.id, context.user_data['product_id'], amount, customer_info, service_type))
    order_id = c.lastrowid
    conn.commit()

    # إرسال إشعار للمدير
    # تحديد نوع الطلب
    service_name = ""
    if context.user_data.get('selected_mega'):
        c.execute('SELECT name FROM megas WHERE id = ?', (context.user_data['selected_mega'],))
        mega = c.fetchone()
        if mega:
            service_name = mega[0]
    elif context.user_data.get('selected_speed'):
        c.execute('SELECT name FROM speeds WHERE id = ?', (context.user_data['selected_speed'],))
        speed = c.fetchone()
        if speed:
            service_name = speed[0]
    else:
        service_name = "دفعة يدوية"
        # تعيين نوع الخدمة كدفعة يدوية في حالة الدفع اليدوي
        c.execute('UPDATE orders SET note = ? WHERE id = ?', ("دفعة يدوية", order_id))

    # إرسال إشعار للمدير
    admin_message = f"""
طلب جديد
رقم الطلب: {order_id}
معرف المشتري: {update.effective_user.id}
الشركة: {product_name}
الخدمة: {service_name}
المبلغ: {amount} ليرة سوري
بيانات الزبون: {customer_info}
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
    if query.data == 'back':
        await start(update, context)
    return ConversationHandler.END

async def handle_new_order_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.text
    try:
        user_id = int(user_id)
        context.user_data['new_order_user_id'] = user_id
        await update.message.reply_text("أدخل بيانات الزبون:")
        return "WAITING_NEW_ORDER_CUSTOMER_INFO"
    except ValueError:
        await update.message.reply_text("معرف مستخدم غير صحيح، الرجاء المحاولة مرة أخرى.")
        return ConversationHandler.END

async def handle_new_order_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split('_')[2])
    context.user_data['new_order_product_id'] = product_id
    await query.message.edit_text("أدخل بيانات الزبون:")
    return "WAITING_NEW_ORDER_CUSTOMER_INFO"

async def handle_new_order_customer_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    customer_info = update.message.text
    context.user_data['customer_info'] = customer_info
    await update.message.reply_text("الرجاء إدخال المبلغ:")
    return "WAITING_AMOUNT"

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
    await update.message.reply_text(f"تمتحديث مبلغ الطلب {order_id} إلى {new_amount}")

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

        # إضافة جدول الباقات إذا لم يكن موجوداً
        c.execute('''CREATE TABLE IF NOT EXISTS packages
                     (id INTEGER PRIMARY KEY, product_id INTEGER, name TEXT, price REAL, 
                      is_active BOOLEAN DEFAULT 1,
                      FOREIGN KEY(product_id) REFERENCES products(id))''')

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

        try:
            # استرجاع الباقات مع أسماء المنتجات
            c.execute('''
                SELECT p.id, p.name, p.price, p.product_id, pr.name as product_name, p.is_active 
                FROM packages p
                JOIN products pr ON p.product_id = pr.id
                ORDER BY p.id DESC
            ''')
            packages = [
                {
                    'id': row[0],
                    'name': row[1],
                    'price': row[2],
                    'product_id': row[3],
                    'product_name': row[4],
                    'is_active': row[5]
                }
                for row in c.fetchall()
            ]
        except Exception as e:
            print(f"Error fetching packages: {str(e)}")
            packages = []

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

        # Fetch speeds with product names
        c.execute('''
            SELECT s.id, s.name, s.price, s.product_id, p.name as product_name, s.is_active
            FROM speeds s
            JOIN products p ON s.product_id = p.id
            ORDER BY s.id DESC
        ''')
        speeds = [
            {
                'id': row[0],
                'name': row[1],
                'price': row[2],
                'product_id': row[3],
                'product_name': row[4],
                'is_active': row[5]
            }
            for row in c.fetchall()
        ]

        # Fetch megas with product names
        c.execute('''
            SELECT m.id, m.name, m.price, m.product_id, p.name as product_name, m.is_active
            FROM megas m
            JOIN products p ON m.product_id = p.id
            ORDER BY m.id DESC
        ''')
        megas = [
            {
                'id': row[0],
                'name': row[1],
                'price': row[2],
                'product_id': row[3],
                'product_name': row[4],
                'is_active': row[5]
            }
            for row in c.fetchall()
        ]
        conn.close()
        return render_template('admin.html', categories=categories, products=products, users=users, orders=orders, speeds=speeds, megas=megas)
    except Exception as e:
        print(f"Error in admin_panel: {str(e)}")
        if conn:
            conn.close()
        return "حدث خطأ في الوصول إلى لوحة التحكم. الرجاء المحاولة مرة أخرى.", 500

@app.route('/add_speed', methods=['POST'])
def add_speed():
    try:
        product_id = request.form['product_id']
        name = request.form['name']
        price = float(request.form['price'])
        is_active = 'is_active' in request.form

        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('INSERT INTO speeds (product_id, name, price, is_active) VALUES (?, ?, ?, ?)',
                 (product_id, name, price, is_active))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in add_speed: {str(e)}")
        return "حدث خطأ في إضافة السرعة", 500

@app.route('/toggle_speed', methods=['POST'])
def toggle_speed():
    try:
        speed_id = request.form['speed_id']
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('UPDATE speeds SET is_active = NOT is_active WHERE id = ?', (speed_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in toggle_speed: {str(e)}")
        return "حدث خطأ في تفعيل/تعطيل السرعة", 500

@app.route('/delete_speed', methods=['POST'])
def delete_speed():
    try:
        speed_id = request.form['speed_id']
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('DELETE FROM speeds WHERE id = ?', (speed_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in delete_speed: {str(e)}")
        return "حدث خطأ في حذف السرعة", 500

@app.route('/edit_speed', methods=['POST'])
def edit_speed():
    try:
        speed_id = request.form['speed_id']
        name = request.form['name']
        price = float(request.form['price'])
        product_id = request.form['product_id'] # Added product_id
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('UPDATE speeds SET name = ?, price = ?, product_id = ? WHERE id = ?',
                 (name, price, product_id, speed_id)) # Updated query to include product_id
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in edit_speed: {str(e)}")
        return "حدث خطأ في تعديل السرعة", 500

@app.route('/add_mega', methods=['POST'])
def add_mega():
    try:
        product_id = request.form['product_id']
        name = request.form['name']
        price = float(request.form['price'])
        is_active = 'is_active' in request.form

        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('INSERT INTO megas (product_id, name, price, is_active) VALUES (?, ?, ?, ?)',
                 (product_id, name, price, is_active))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in add_mega: {str(e)}")
        return "حدث خطأ في إضافة الميغا", 500

@app.route('/edit_mega', methods=['POST'])
def edit_mega():
    try:
        mega_id = request.form['mega_id']
        name = request.form['name']
        price = float(request.form['price'])
        product_id = request.form['product_id']
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('UPDATE megas SET name = ?, price = ?, product_id = ? WHERE id = ?',
                 (name, price, product_id, mega_id))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in edit_mega: {str(e)}")
        return "حدث خطأ في تعديل الميغا", 500

@app.route('/toggle_mega', methods=['POST'])
def toggle_mega():
    try:
        mega_id = request.form['mega_id']
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('UPDATE megas SET is_active = NOT is_active WHERE id = ?', (mega_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in toggle_mega: {str(e)}")
        return "حدث خطأ في تفعيل/تعطيل الميغا", 500

@app.route('/delete_mega', methods=['POST'])
def delete_mega():
    try:
        mega_id = request.form['mega_id']
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('DELETE FROM megas WHERE id = ?', (mega_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in delete_mega: {str(e)}")
        return "حدث خطأ في حذف الميغا", 500

@app.route('/add_package', methods=['POST'])
def add_package():
    try:
        product_id = request.form['product_id']
        name = request.form['name']
        price = float(request.form['price'])

        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('INSERT INTO packages (product_id, name, price, is_active) VALUES (?, ?, ?, 1)',
                 (product_id, name, price))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel'))
    except Exception as e:
        print(f"Error in add_package: {str(e)}")
        return "حدث خطأ في إضافة الباقة", 500

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
                     (id INTEGER PRIMARY KEY,name TEXT, category TEXT, is_active BOOLEAN DEFAULT 1)''')
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
    """
    دالة محسنة لإرسال الإشعارات للمستخدمين مع التأكد من الإرسال
    """
    MAX_RETRIES = 5
    RETRY_DELAY = 2  # ثانية

    async def send_single_message(bot, chat_id, retry_count=0):
        try:
            await bot.send_message(
                chat_id=int(chat_id),
                text=message,
                parse_mode='HTML',
                disable_notification=not is_important
            )
            print(f"✅ تم إرسال الإشعار بنجاح للمستخدم {chat_id}")
            return True
        except telegram.error.RetryAfter as e:
            print(f"⏳ انتظار قبل إعادة المحاولة للمستخدم {chat_id}")
            if retry_count < MAX_RETRIES:
                await asyncio.sleep(e.retry_after)
                return await send_single_message(bot, chat_id, retry_count + 1)
            return False
        except telegram.error.BadRequest as e:
            print(f"❌ خطأ في إرسال الرسالة للمستخدم {chat_id}: {str(e)}")
            try:
                # محاولة إرسال بدون تنسيق
                await bot.send_message(
                    chat_id=int(chat_id),
                    text=message,
                    disable_notification=not is_important
                )
                return True
            except Exception as inner_e:
                print(f"❌ فشل الإرسال البديل للمستخدم {chat_id}: {str(inner_e)}")
                return False
        except telegram.error.Unauthorized:
            print(f"🚫 المستخدم {chat_id} قام بحظر البوت")
            return False
        except Exception as e:
            print(f"❌ خطأ غير متوقع للمستخدم {chat_id}: {str(e)}")
            if retry_count < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
                return await send_single_message(bot, chat_id, retry_count + 1)
            return False

    try:
        bot = telegram.Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
        
        if user_id:
            # إرسال لمستخدم محدد
            success = await send_single_message(bot, user_id)
            if not success:
                print(f"⚠️ فشل إرسال الإشعار للمستخدم {user_id} بعد كل المحاولات")
            return success
        else:
            # إرسال لجميع المستخدمين النشطين
            conn = sqlite3.connect('store.db')
            c = conn.cursor()
            c.execute('SELECT telegram_id FROM users WHERE is_active = 1')
            users = c.fetchall()
            conn.close()

            success_count = 0
            total_users = len(users)

            for user in users:
                if await send_single_message(bot, user[0]):
                    success_count += 1
                await asyncio.sleep(0.5)  # تأخير بين الرسائل

            print(f"📊 تم إرسال الإشعارات بنجاح لـ {success_count} من {total_users} مستخدم")
            return success_count > 0

    except Exception as e:
        print(f"❌ خطأ في نظام الإشعارات: {str(e)}")
        return False

    try:
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not bot_token:
            print("❌ خطأ: لم يتم العثور على توكن البوت")
            return False

        bot = telegram.Bot(token=bot_token)

        if user_id:
            # إرسال لمستخدم واحد
            return await send_single_message(bot, user_id)
        else:
            # إرسال لجميع المستخدمين النشطين
            async with asyncio.Semaphore(5):  # تحديد عدد الإرسالات المتزامنة
                conn = sqlite3.connect('store.db')
                c = conn.cursor()
                c.execute('SELECT telegram_id FROM users WHERE is_active = 1')
                users = c.fetchall()
                conn.close()

                success_count = 0
                total_users = len(users)

                for user in users:
                    if await send_single_message(bot, user[0]):
                        success_count += 1
                    await asyncio.sleep(0.1)  # تأخير بين الرسائل لتجنب التقييد

                print(f"📊 تم إرسال الإشعارات بنجاح لـ {success_count} من {total_users} مستخدم")
                return success_count > 0

    except Exception as e:
        print(f"❌ خطأ في نظام الإشعارات: {str(e)}")
        return False

@app.route('/send_notification', methods=['POST'])
def send_notification_route():
    try:
        message = request.form['message']
        notification_type = request.form.get('notification_type', 'all')
        user_id = request.form.get('user_id')
        button_texts = request.form.getlist('button_text[]')
        button_types = request.form.getlist('button_type[]')
        button_values = request.form.getlist('button_value[]')
        
        # تجهيز الأزرار
        keyboard = []
        if button_texts:
            row = []
            for text, type_, value in zip(button_texts, button_types, button_values):
                if type_ == 'url':
                    button = InlineKeyboardButton(text, url=value)
                else:
                    button = InlineKeyboardButton(text, callback_data=value)
                row.append(button)
                if len(row) == 2:  # عرض زرين في كل صف
                    keyboard.append(row)
                    row = []
            if row:  # إضافة الأزرار المتبقية
                keyboard.append(row)

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        async def send_notifications():
            bot = telegram.Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
            conn = sqlite3.connect('store.db')
            c = conn.cursor()

            try:
                if notification_type == 'individual' and user_id:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=message,
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
                else:
                    c.execute('SELECT telegram_id FROM users WHERE is_active = 1')
                    users = c.fetchall()
                    for user in users:
                        try:
                            await bot.send_message(
                                chat_id=user[0],
                                text=message,
                                parse_mode='Markdown',
                                reply_markup=reply_markup
                            )
                        except Exception as e:
                            print(f"Error sending message to {user[0]}: {e}")
            finally:
                conn.close()

        asyncio.run(send_notifications())
        return redirect(url_for('admin_panel'))

    except Exception as e:
        print(f"Error in send_notification_route: {str(e)}")
        return "حدث خطأ في إرسال الإشعار", 500

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
            asyncio.run(send_notification(bot, notification_message, user_id))
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
        asyncio.run(send_notification(bot, notification_message, user_id))
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
            asyncio.run(send_notification(bot, notification_message, user_id))
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
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # استرجاع معلومات الطلب والمنتج
        c.execute('''
            SELECT o.user_id, o.amount, p.name
            FROM orders o
            JOIN products p ON o.product_id = p.id 
            WHERE o.id = ?
        ''', (order_id,))

        order_info = c.fetchone()
        if not order_info:
            return "الطلب غير موجود", 404

        user_id = order_info[0]
        amount = order_info[1]
        product_name = order_info[2]

        # تحديث حالة الطلب
        c.execute('UPDATE orders SET status = ?, note = ?, rejection_note =? WHERE id= ?',
                 (new_status, note, rejection_note if new_status == 'rejected' else None, order_id))

        # إعداد نص الإشعار
        if new_status == "accepted":
            notification_message = f"""✅ تم قبول طلبك
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ: {amount} ليرة سوري"""
        elif new_status == "rejected":
            notification_message = f"""❌ تم رفض طلبك
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ: {amount} ليرة سوري"""
            if rejection_note:
                notification_message += f"\nسبب الرفض: {rejection_note}"
        else:
            notification_message = f"""🕒 تم تحديث حالة طلبك
رقم الطلب: {order_id}
الشركة: {product_name}
الحالة: قيد المعالجة"""

        # تم إخفاء الملاحظات

        # إرسال الإشعار للمستخدم
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        bot = telegram.Bot(token=bot_token)
        asyncio.run(send_notification(bot, notification_message, user_id))

        conn.commit()

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
        notification_message = ""
        if new_status == "accepted":
            notification_message = f"""✅ تم قبول طلبك!
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ: {amount} ليرة سوري"""
        elif new_status == "rejected":
            notification_message = f"""❌ تم رفض طلبك وإعادة المبلغ لرصيدك
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ المعاد: {amount} ليرة سوري"""
            if rejection_note:
                notification_message += f"\nسبب الرفض: {rejection_note}"
        else:
            notification_message = f"""🕒 تم تحديث حالة طلبك
رقم الطلب: {order_id}
الشركة: {product_name}
الحالة الجديدة: قيد المعالجة"""

        # تم إخفاء الملاحظات

        # إرسال الإشعار للمستخدم
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        bot = telegram.Bot(token=bot_token)
        try:
            asyncio.run(send_notification(bot, notification_message, user_id))
        except Exception as e:
            print(f"خطأ في إرسال الإشعار: {str(e)}")

        conn.commit()
        return redirect(url_for('admin_panel'))

    except Exception as e:
        print(f"Error in change_order_status: {str(e)}")
        return f"حدث خطأ في تغيير حالة الطلب: {str(e)}", 500
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                print(f"Error closing connection: {str(e)}")
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

        # استرجاع بيانات الزبون
        c.execute('SELECT customer_info FROM orders WHERE id = ?', (order_id,))
        customer_info = c.fetchone()[0]

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
بيانات الزبون: {customer_info}
سبب الرفض: {rejection_note}
رصيدك الحالي: {current_balance + amount} ليرة سوري"""

        elif action == 'accept':
            c.execute('UPDATE orders SET status = ? WHERE id = ?', 
                    ('accepted', order_id))

            # إعداد رسالة الإشعار للقبول
            notification_message = f"""✅ تم قبول طلبك!
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ: {amount} ليرة سوري
بيانات الزبون: {customer_info}"""

        # إرسال الإشعار للمستخدم
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        bot = telegram.Bot(token=bot_token)
        try:
            asyncio.run(send_notification(bot, notification_message, user_id))
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
        c.execute('''SELECT o.amount, o.user_id, o.status, p.name, u.balance
                     FROM orders o 
                     JOIN products p ON o.product_id = p.id 
                     JOIN users u ON o.user_id = u.telegram_id
                     WHERE o.id = ?''', (order_id,))
        current_order = c.fetchone()

        if not current_order:
            conn.close()
            return "الطلب غير موجود", 404

        current_amount = current_order[0]
        user_id = current_order[1]
        status = current_order[2]
        product_name = current_order[3]

        # إذا كان الطلب مقبولاً أو قيد المعالجة، نتعامل مع الرصيد
        if status != 'rejected':
            amount_diff = new_amount - current_amount

            if amount_diff > 0:  # إذا كان المبلغ الجديد أكبر
                if current_order[4] < amount_diff:  # التحقق من الرصيد الحالي
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

        # إعداد رسالة الإشعار
        notification_message = f"""تم تعديل مبلغ الطلب
رقم الطلب: {order_id}
الشركة: {product_name}
المبلغ الجديد: {new_amount} ليرة سوري"""

        # حفظ التغييرات في قاعدة البيانات
        conn.commit()

        try:
            # إنشاء مثيل جديد للبوت وإرسال الإشعار
            bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
            if bot_token:
                # استخدام requests بدلاً من telegram-python-bot
                telegram_api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                payload = {
                    "chat_id": user_id,
                    "text": notification_message
                }
                requests.post(telegram_api_url, json=payload)
        except Exception as e:
            print(f"Error sending notification: {str(e)}")

        conn.close()
        return redirect(url_for('admin_panel'))

    except Exception as e:
        print(f"خطأ في تعديل مبلغ الطلب: {str(e)}")
        if 'conn' in locals() and conn:
            conn.close()
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
            "WAITING_NEW_ORDER_PRODUCT": [
                CallbackQueryHandler(handle_new_order_product, pattern="^select_product__"),
                CallbackQueryHandler(button_click, pattern="^back$")
            ],
            "WAITING_NEW_ORDER_CUSTOMER_INFO": [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_order_customer_info),
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
        bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        bot = telegram.Bot(token=bot_token)
        asyncio.run(send_notification(bot, f"تم إضافة {amount} ليرة سوري إلى رصيدك من قبل الموزع", user_id))

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