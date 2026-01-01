#!/usr/bin/env python3
"""
Скрипт для автоматической генерации и отправки Excel отчета на email
Запускается по расписанию (cron) каждые 2 часа
"""
import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Настройки email из переменных окружения
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.mail.ru")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
REPORT_EMAIL = os.getenv("REPORT_EMAIL", "xasanimbuiss@mail.ru")

def send_email_with_attachment(file_path: str, recipient: str) -> bool:
    """
    Отправляет email с вложенным файлом
    
    Args:
        file_path: Путь к файлу для отправки
        recipient: Email получателя
        
    Returns:
        True если успешно, False если ошибка
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        print("❌ Ошибка: SMTP_USER и SMTP_PASSWORD должны быть установлены в .env")
        return False
    
    try:
        # Создаем сообщение
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = recipient
        msg['Subject'] = f"Отчет по базе данных бота - {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        
        # Текст письма
        body = f"""Здравствуйте!

Автоматически сформированный отчет по базе данных Telegram-бота.

Дата формирования: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}

Отчет содержит:
- Сводную статистику
- Список пользователей
- Историю платежей
- Информацию о подписках
- Пригласительные ссылки

Файл прикреплен к письму.

---
Это автоматическое сообщение, не отвечайте на него.
        """
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        # Прикрепляем файл
        with open(file_path, 'rb') as attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
            encoders.encode_base64(part)
            part.add_header(
                'Content-Disposition',
                f'attachment; filename= {os.path.basename(file_path)}'
            )
            msg.attach(part)
        
        # Отправляем email
        print(f"📧 Подключение к SMTP серверу {SMTP_SERVER}:{SMTP_PORT}...")
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()  # Включаем TLS
        server.login(SMTP_USER, SMTP_PASSWORD)
        text = msg.as_string()
        server.sendmail(SMTP_USER, recipient, text)
        server.quit()
        
        print(f"✅ Email успешно отправлен на {recipient}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при отправке email: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Главная функция"""
    print("=" * 60)
    print("📊 Генерация и отправка Excel отчета")
    print("=" * 60)
    
    try:
        # Импортируем функцию генерации отчета
        from generate_excel_report import main as generate_report
        
        # Генерируем отчет
        print(f"\n📝 Генерация отчета...")
        report_file = generate_report()
        
        if not report_file or not os.path.exists(report_file):
            print("❌ Ошибка: Не удалось создать отчет")
            sys.exit(1)
        
        # Отправляем email
        print(f"\n📧 Отправка отчета на {REPORT_EMAIL}...")
        if send_email_with_attachment(report_file, REPORT_EMAIL):
            # Удаляем файл после успешной отправки
            try:
                os.remove(report_file)
                print(f"🗑️  Временный файл удален: {report_file}")
            except Exception as e:
                print(f"⚠️  Не удалось удалить временный файл: {e}")
        else:
            print("❌ Не удалось отправить email, файл сохранен для повторной попытки")
            sys.exit(1)
        
        print("\n✅ Процесс завершен успешно!")
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
