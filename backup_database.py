#!/usr/bin/env python3
"""
Скрипт для автоматического резервного копирования базы данных
Отправляет сжатую копию базы данных на email каждые 2 часа
"""
import os
import sys
import sqlite3
import gzip
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from dotenv import load_dotenv
import tempfile

load_dotenv()

# Настройки из переменных окружения
DB_PATH = os.getenv("DB_PATH", "/opt/bot_telegram/bot.db")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.mail.ru")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
BACKUP_EMAIL = os.getenv("REPORT_EMAIL", "xasanimbuiss@mail.ru")

def create_backup() -> str:
    """
    Создает резервную копию базы данных
    
    Returns:
        Путь к созданному backup файлу
    """
    if not os.path.exists(DB_PATH):
        print(f"❌ Ошибка: База данных не найдена: {DB_PATH}")
        sys.exit(1)
    
    # Создаем временный файл для backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"bot_db_backup_{timestamp}.db.gz"
    backup_path = os.path.join(tempfile.gettempdir(), backup_filename)
    
    print(f"📦 Создание резервной копии базы данных...")
    print(f"   Исходный файл: {DB_PATH}")
    print(f"   Размер исходного файла: {os.path.getsize(DB_PATH) / 1024:.2f} KB")
    
    # Копируем базу данных и сжимаем
    try:
        with open(DB_PATH, 'rb') as f_in:
            with gzip.open(backup_path, 'wb') as f_out:
                f_out.writelines(f_in)
        
        backup_size = os.path.getsize(backup_path)
        print(f"✅ Резервная копия создана: {backup_path}")
        print(f"   Размер сжатого файла: {backup_size / 1024:.2f} KB")
        print(f"   Сжатие: {(1 - backup_size / os.path.getsize(DB_PATH)) * 100:.1f}%")
        
        return backup_path
    except Exception as e:
        print(f"❌ Ошибка при создании резервной копии: {e}")
        sys.exit(1)

def verify_backup(backup_path: str) -> bool:
    """
    Проверяет целостность резервной копии
    
    Args:
        backup_path: Путь к backup файлу
        
    Returns:
        True если backup валиден, False иначе
    """
    try:
        # Распаковываем и проверяем, что это валидная SQLite база
        with gzip.open(backup_path, 'rb') as f:
            # Читаем первые 16 байт (SQLite magic header)
            header = f.read(16)
            if header == b'SQLite format 3\x00':
                print("✅ Резервная копия валидна (SQLite формат подтвержден)")
                return True
            else:
                print("❌ Резервная копия повреждена (неверный формат)")
                return False
    except Exception as e:
        print(f"❌ Ошибка при проверке резервной копии: {e}")
        return False

def send_backup_email(backup_path: str, recipient: str) -> bool:
    """
    Отправляет резервную копию на email
    
    Args:
        backup_path: Путь к backup файлу
        recipient: Email получателя
        
    Returns:
        True если успешно, False иначе
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        print("❌ Ошибка: SMTP_USER и SMTP_PASSWORD должны быть установлены в .env")
        return False
    
    try:
        # Создаем сообщение
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = recipient
        msg['Subject'] = f"Резервная копия базы данных - {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        
        # Текст письма
        backup_size = os.path.getsize(backup_path) / 1024
        db_size = os.path.getsize(DB_PATH) / 1024
        
        body = f"""Здравствуйте!

Автоматическая резервная копия базы данных Telegram-бота.

📅 Дата создания: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}
📦 Размер базы данных: {db_size:.2f} KB
🗜️  Размер сжатого файла: {backup_size:.2f} KB
📊 Сжатие: {(1 - backup_size / os.path.getsize(DB_PATH)) * 100:.1f}%

⚠️ ВАЖНО: Сохраните этот файл в безопасном месте!
Это полная копия базы данных со всеми подписками, платежами и пользователями.

Для восстановления:
1. Распакуйте файл (gunzip или 7-Zip)
2. Замените bot.db на сервере
3. Перезапустите сервисы

---
Это автоматическое сообщение, не отвечайте на него.
        """
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # Прикрепляем файл
        with open(backup_path, 'rb') as attachment:
            part = MIMEBase('application', 'gzip')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename= {os.path.basename(backup_path)}'
            )
            msg.attach(part)
        
        # Отправляем email
        print(f"📧 Отправка резервной копии на {recipient}...")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        text = msg.as_string()
        server.sendmail(SMTP_USER, recipient, text)
        server.quit()
        
        print(f"✅ Резервная копия успешно отправлена на {recipient}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при отправке email: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Главная функция"""
    print("=" * 60)
    print("💾 Резервное копирование базы данных")
    print("=" * 60)
    
    try:
        # Создаем резервную копию
        backup_path = create_backup()
        
        # Проверяем целостность
        if not verify_backup(backup_path):
            print("❌ Резервная копия повреждена, отправка отменена")
            os.remove(backup_path)
            sys.exit(1)
        
        # Отправляем на email
        if send_backup_email(backup_path, BACKUP_EMAIL):
            # Удаляем файл после успешной отправки
            try:
                os.remove(backup_path)
                print(f"🗑️  Временный файл удален: {backup_path}")
            except Exception as e:
                print(f"⚠️  Не удалось удалить временный файл: {e}")
        else:
            print("❌ Не удалось отправить резервную копию, файл сохранен для повторной попытки")
            print(f"   Файл: {backup_path}")
            sys.exit(1)
        
        print("\n✅ Резервное копирование завершено успешно!")
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

