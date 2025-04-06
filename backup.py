
import sqlite3
import json
import os
from datetime import datetime
import shutil

def sync_from_deployed():
    """تحديث قاعدة البيانات المحلية من النسخة المنشورة"""
    try:
        # التأكد من إغلاق أي اتصالات مفتوحة مع قاعدة البيانات
        import sqlite3
        try:
            conn = sqlite3.connect('store.db')
            conn.close()
        except:
            pass
            
        # نسخ قاعدة البيانات من النسخة المنشورة
        if os.path.exists('store.db'):
            # عمل نسخة احتياطية من النسخة المحلية
            backup_time = datetime.now().strftime("%Y%m%d_%H%M%S")
            os.makedirs('local_backups', exist_ok=True)
            shutil.copy2('store.db', f'local_backups/store_backup_{backup_time}.db')
            
        shutil.copy2('backup_20250405_181443/store.db', 'store.db')
        print("تم تحديث قاعدة البيانات المحلية بنجاح")
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

if __name__ == "__main__":
    create_backup()
