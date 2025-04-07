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
        backup_folders = [d for d in os.listdir('.') if d.startswith('backup_') and os.path.isdir(d)]
        if not backup_folders:
            raise Exception("لم يتم العثور على مجلد النسخ الاحتياطية")

        latest_backup = max(backup_folders)
        deployed_db = f'{latest_backup}/store.db'

        if not os.path.exists(deployed_db):
            raise Exception("لم يتم العثور على قاعدة البيانات المنشورة")

        # دمج البيانات بدلاً من النسخ المباشر
        merge_databases(deployed_db, 'store.db')
        print(f"تم مزامنة البيانات من النسخة المنشورة: {deployed_db}")

    except Exception as e:
        print(f"خطأ في مزامنة البيانات: {str(e)}")

if __name__ == "__main__":
    create_backup()