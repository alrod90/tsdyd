
import sqlite3
import json
import os
from datetime import datetime
import shutil

def sync_from_deployed():
    """مزامنة البيانات بين النسخة المحلية والمنشورة"""
    try:
        import sqlite3
        import json
        import os
        from datetime import datetime
        import shutil

        # إغلاق أي اتصالات مفتوحة
        try:
            conn = sqlite3.connect('store.db')
            conn.close()
        except:
            pass

        # حفظ نسخة احتياطية من قاعدة البيانات الحالية
        if os.path.exists('store.db'):
            backup_time = datetime.now().strftime("%Y%m%d_%H%M%S")
            os.makedirs('local_backups', exist_ok=True)
            shutil.copy2('store.db', f'local_backups/store_backup_{backup_time}.db')
            print(f"تم حفظ نسخة احتياطية: store_backup_{backup_time}.db")

        # البحث عن أحدث نسخة في مجلد النسخ الاحتياطية
        backup_folders = [d for d in os.listdir('.') if d.startswith('backup_') and os.path.isdir(d)]
        if not backup_folders:
            raise Exception("لم يتم العثور على مجلد النسخ الاحتياطية")
            
        latest_backup = max(backup_folders)
        deployed_db = f'{latest_backup}/store.db'
        
        if not os.path.exists(deployed_db):
            raise Exception("لم يتم العثور على قاعدة البيانات المنشورة")
            
        # نسخ قاعدة البيانات المنشورة
        shutil.copy2(deployed_db, 'store.db')
        
        # نقل البيانات من النسخة المنشورة إلى النسخة المحلية
        deployed_conn = sqlite3.connect(deployed_db)
        local_conn = sqlite3.connect('store.db')
        
        # نقل بيانات المنتجات
        deployed_conn.execute("ATTACH DATABASE 'store.db' AS local")
        deployed_conn.execute("INSERT OR REPLACE INTO local.products SELECT * FROM products")
        
        # نقل بيانات المستخدمين
        deployed_conn.execute("INSERT OR REPLACE INTO local.users SELECT * FROM users")
        
        # نقل بيانات الطلبات
        deployed_conn.execute("INSERT OR REPLACE INTO local.orders SELECT * FROM orders")
        
        deployed_conn.commit()
        deployed_conn.close()
        local_conn.close()
        
        print(f"تم نقل جميع البيانات من النسخة المنشورة: {deployed_db}")
        
    except Exception as e:
        print(f"حدث خطأ أثناء التحديث: {str(e)}")

def create_backup():
    # إنشاء مجلد للنسخ الاحتياطي
    backup_dir = "backup_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(backup_dir, exist_ok=True)
    
    # نسخ قاعدة البيانات
    conn = sqlite3.connect('store.db')
    backup_conn = sqlite3.connect(f'{backup_dir}/store.db')
    conn.backup(backup_conn)
    conn.close()
    backup_conn.close()
    
    # نسخ الملفات
    files_to_backup = ['main.py', 'templates/admin.html', 'templates/login.html']
    for file in files_to_backup:
        if os.path.exists(file):
            os.makedirs(os.path.dirname(f'{backup_dir}/{file}'), exist_ok=True)
            shutil.copy2(file, f'{backup_dir}/{file}')
    
    print(f"تم إنشاء النسخة الاحتياطية في المجلد: {backup_dir}")

def auto_sync():
    """مزامنة تلقائية كل دقيقة"""
    while True:
        try:
            sync_from_deployed()
            time.sleep(60)  # انتظار دقيقة واحدة
        except Exception as e:
            print(f"خطأ في المزامنة التلقائية: {str(e)}")
            time.sleep(60)  # انتظار دقيقة واحدة في حالة الخطأ

if __name__ == "__main__":
    import threading
    import time
    
    # بدء المزامنة التلقائية في خلفية البرنامج
    sync_thread = threading.Thread(target=auto_sync, daemon=True)
    sync_thread.start()
    
    # إنشاء النسخة الاحتياطية
    create_backup()
