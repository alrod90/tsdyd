import sqlite3
import json
import os
from datetime import datetime
import shutil

def merge_databases(source_db, target_db):
    """دمج قاعدتي بيانات مع الحفاظ على البيانات الموجودة"""
    try:
        source_conn = sqlite3.connect(source_db)
        target_conn = sqlite3.connect(target_db)

        # نسخ البيانات الجديدة مع تجنب التكرار
        target_conn.execute("ATTACH DATABASE ? AS source", (source_db,))

        # دمج المنتجات
        target_conn.execute("""
            INSERT OR IGNORE INTO main.products 
            SELECT * FROM source.products
        """)

        # دمج المستخدمين مع تحديث الأرصدة
        target_conn.execute("""
            INSERT OR IGNORE INTO main.users 
            SELECT * FROM source.users
        """)

        # دمج الطلبات الجديدة فقط
        target_conn.execute("""
            INSERT OR IGNORE INTO main.orders 
            SELECT * FROM source.orders
        """)

        target_conn.commit()
        target_conn.close()
        source_conn.close()
        print("تم دمج قواعد البيانات بنجاح")
    except Exception as e:
        print(f"خطأ في دمج قواعد البيانات: {str(e)}")

def create_backup():
    """إنشاء نسخة احتياطية مع دمج البيانات"""
    # إنشاء مجلد للنسخ الاحتياطي
    backup_dir = "backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(backup_dir, exist_ok=True)

    # البحث عن آخر نسخة احتياطية
    backup_folders = [d for d in os.listdir('.') if d.startswith('backup_') and os.path.isdir(d)]
    if backup_folders:
        latest_backup = max(backup_folders)
        old_db = f'{latest_backup}/store.db'
        if os.path.exists(old_db):
            # نسخ قاعدة البيانات القديمة أولاً
            shutil.copy2(old_db, f'{backup_dir}/store.db')
            # دمج البيانات الجديدة
            merge_databases('store.db', f'{backup_dir}/store.db')
        else:
            shutil.copy2('store.db', f'{backup_dir}/store.db')
    else:
        shutil.copy2('store.db', f'{backup_dir}/store.db')

    # نسخ الملفات الأخرى
    files_to_backup = ['main.py', 'templates/admin.html', 'templates/login.html']
    for file in files_to_backup:
        if os.path.exists(file):
            os.makedirs(os.path.dirname(f'{backup_dir}/{file}'), exist_ok=True)
            shutil.copy2(file, f'{backup_dir}/{file}')

    print(f"تم إنشاء النسخة الاحتياطية في المجلد: {backup_dir}")

def sync_from_deployed():
    """مزامنة البيانات من النسخة المنشورة"""
    try:
        import requests
        
        # الاتصال بالنسخة المنشورة للحصول على قاعدة البيانات
        response = requests.get('https://alrod.replit.app/get_db')
        
        if response.status_code == 200:
            # عمل نسخة احتياطية من قاعدة البيانات المحلية
            if os.path.exists('store.db'):
                backup_dir = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                os.makedirs(backup_dir, exist_ok=True)
                shutil.copy2('store.db', f'{backup_dir}/store.db')
                print(f"تم عمل نسخة احتياطية في: {backup_dir}")
            
            # استبدال قاعدة البيانات المحلية بالمنشورة
            with open('store.db', 'wb') as f:
                f.write(response.content)
            
            print("تم جلب واستبدال البيانات من النسخة المنشورة بنجاح")
        else:
            print(f"فشل في الاتصال بالنسخة المنشورة: {response.status_code}")
        print(f"تم مزامنة البيانات من النسخة المنشورة: {deployed_db}")

    except Exception as e:
        print(f"خطأ في مزامنة البيانات: {str(e)}")

if __name__ == "__main__":
    create_backup()