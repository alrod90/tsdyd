
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.core.window import Window
from kivy.uix.spinner import Spinner
from kivy.clock import Clock
import sqlite3
import os
import requests
import json

class BillPaymentApp(App):
    def build(self):
        # تعيين خلفية التطبيق
        Window.clearcolor = (0.9, 0.9, 0.9, 1)
        
        # التخطيط الرئيسي
        self.main_layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        
        # تسجيل الدخول
        self.login_layout = BoxLayout(orientation='vertical', spacing=10, size_hint_y=None, height=200)
        self.telegram_id = TextInput(
            hint_text='معرف التيلجرام',
            multiline=False,
            size_hint_y=None,
            height=40
        )
        self.login_button = Button(
            text='تسجيل الدخول',
            size_hint_y=None,
            height=50,
            background_color=(0.2, 0.7, 0.3, 1),
            on_press=self.login
        )
        self.login_layout.add_widget(self.telegram_id)
        self.login_layout.add_widget(self.login_button)
        
        # القائمة الرئيسية (مخفية في البداية)
        self.menu_layout = BoxLayout(orientation='vertical', spacing=10)
        self.menu_layout.opacity = 0
        
        # عرض الرصيد
        self.balance_label = Label(
            text='',
            size_hint_y=None,
            height=50
        )
        
        # قائمة الأقسام
        self.categories_layout = GridLayout(cols=2, spacing=10, size_hint_y=None)
        self.categories_layout.bind(minimum_height=self.categories_layout.setter('height'))
        
        # منطقة الطلبات
        self.orders_layout = BoxLayout(orientation='vertical', spacing=10)
        self.orders_scroll = ScrollView(size_hint=(1, None), size=(Window.width, Window.height * 0.4))
        self.orders_grid = GridLayout(cols=1, spacing=10, size_hint_y=None)
        self.orders_grid.bind(minimum_height=self.orders_grid.setter('height'))
        self.orders_scroll.add_widget(self.orders_grid)
        
        # إضافة كل العناصر للتخطيط الرئيسي
        self.menu_layout.add_widget(self.balance_label)
        self.menu_layout.add_widget(self.categories_layout)
        self.menu_layout.add_widget(self.orders_scroll)
        
        self.main_layout.add_widget(self.login_layout)
        self.main_layout.add_widget(self.menu_layout)
        
        # تحديث البيانات كل 30 ثانية
        Clock.schedule_interval(self.update_data, 30)
        
        return self.main_layout

    def login(self, instance):
        try:
            user_id = int(self.telegram_id.text)
            conn = sqlite3.connect('store.db')
            c = conn.cursor()
            c.execute('SELECT balance, is_active FROM users WHERE telegram_id = ?', (user_id,))
            result = c.fetchone()
            
            if result and result[1]:  # التحقق من وجود المستخدم وأنه نشط
                self.current_user_id = user_id
                self.login_layout.opacity = 0
                self.menu_layout.opacity = 1
                self.update_data(None)
            else:
                self.show_message("معرف غير صحيح أو الحساب معطل")
                
            conn.close()
        except ValueError:
            self.show_message("الرجاء إدخال معرف صحيح")

    def update_data(self, dt):
        if hasattr(self, 'current_user_id'):
            self.update_balance()
            self.update_categories()
            self.update_orders()

    def update_balance(self):
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT balance FROM users WHERE telegram_id = ?', (self.current_user_id,))
        result = c.fetchone()
        conn.close()
        
        if result:
            self.balance_label.text = f'رصيدك: {result[0]} ليرة سورية'

    def update_categories(self):
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT name, identifier FROM categories WHERE is_active = 1')
        categories = c.fetchall()
        conn.close()
        
        self.categories_layout.clear_widgets()
        for category in categories:
            btn = Button(
                text=category[0],
                size_hint_y=None,
                height=50,
                background_color=(0.3, 0.5, 0.8, 1)
            )
            btn.bind(on_press=lambda x, cat=category[1]: self.show_products(cat))
            self.categories_layout.add_widget(btn)

    def show_products(self, category):
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('SELECT id, name FROM products WHERE category = ? AND is_active = 1', (category,))
        products = c.fetchall()
        conn.close()
        
        # إنشاء نافذة منبثقة للمنتجات
        self.products_layout = GridLayout(cols=1, spacing=10, size_hint_y=None)
        self.products_layout.bind(minimum_height=self.products_layout.setter('height'))
        
        for product in products:
            btn = Button(
                text=product[1],
                size_hint_y=None,
                height=50
            )
            btn.bind(on_press=lambda x, p=product[0]: self.show_product_options(p))
            self.products_layout.add_widget(btn)

    def show_product_options(self, product_id):
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        
        # التحقق من وجود باقات وسرعات
        c.execute('SELECT COUNT(*) FROM speeds WHERE product_id = ? AND is_active = 1', (product_id,))
        has_speeds = c.fetchone()[0] > 0
        
        c.execute('SELECT COUNT(*) FROM megas WHERE product_id = ? AND is_active = 1', (product_id,))
        has_megas = c.fetchone()[0] > 0
        
        conn.close()
        
        # إنشاء نافذة منبثقة للخيارات
        options_layout = GridLayout(cols=1, spacing=10, size_hint_y=None)
        
        if has_speeds:
            speed_btn = Button(text='السرعات', size_hint_y=None, height=50)
            speed_btn.bind(on_press=lambda x: self.show_speeds(product_id))
            options_layout.add_widget(speed_btn)
            
        if has_megas:
            mega_btn = Button(text='الباقات', size_hint_y=None, height=50)
            mega_btn.bind(on_press=lambda x: self.show_megas(product_id))
            options_layout.add_widget(mega_btn)
            
        manual_btn = Button(text='دفعة يدوية', size_hint_y=None, height=50)
        manual_btn.bind(on_press=lambda x: self.show_manual_payment(product_id))
        options_layout.add_widget(manual_btn)

    def update_orders(self):
        conn = sqlite3.connect('store.db')
        c = conn.cursor()
        c.execute('''
            SELECT o.id, p.name, o.amount, o.status, o.created_at 
            FROM orders o 
            JOIN products p ON o.product_id = p.id 
            WHERE o.user_id = ? 
            ORDER BY o.created_at DESC LIMIT 10
        ''', (self.current_user_id,))
        orders = c.fetchall()
        conn.close()
        
        self.orders_grid.clear_widgets()
        for order in orders:
            status = "قيد المعالجة" if order[3] == "pending" else "مقبول" if order[3] == "accepted" else "مرفوض"
            order_label = Label(
                text=f'رقم الطلب: {order[0]}\nالشركة: {order[1]}\nالمبلغ: {order[2]} ل.س\nالحالة: {status}\nالتاريخ: {order[4]}',
                size_hint_y=None,
                height=100
            )
            self.orders_grid.add_widget(order_label)

    def show_message(self, message):
        popup_label = Label(text=message)
        # إظهار رسالة منبثقة

if __name__ == '__main__':
    BillPaymentApp().run()
