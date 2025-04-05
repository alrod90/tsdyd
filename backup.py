
import sqlite3
import json
import os
from datetime import datetime
import shutil

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
