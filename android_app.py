
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
import sqlite3
import json

class BillPaymentApp(App):
    def build(self):
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        
        # عنوان التطبيق
        title = Label(text='نظام تسديد الفواتير', size_hint_y=None, height=50)
        layout.add_widget(title)
        
        # حقل إدخال معرف التيليجرام
        self.user_id = TextInput(hint_text='معرف التيليجرام', multiline=False)
        layout.add_widget(self.user_id)
        
        # زر عرض الرصيد
        check_balance = Button(text='عرض الرصيد', on_press=self.show_balance)
        layout.add_widget(check_balance)
        
        # عرض الرصيد
        self.balance_label = Label(text='')
        layout.add_widget(self.balance_label)
        
        return layout

    def show_balance(self, instance):
        try:
            conn = sqlite3.connect('store.db')
            c = conn.cursor()
            user_id = self.user_id.text
            c.execute('SELECT balance FROM users WHERE telegram_id = ?', (user_id,))
            result = c.fetchone()
            if result:
                self.balance_label.text = f'رصيدك: {result[0]} ليرة سورية'
            else:
                self.balance_label.text = 'لم يتم العثور على المستخدم'
            conn.close()
        except Exception as e:
            self.balance_label.text = 'حدث خطأ في قراءة البيانات'

if __name__ == '__main__':
    BillPaymentApp().run()
