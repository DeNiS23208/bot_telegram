#!/usr/bin/env python3
"""
Скрипт для генерации Excel отчета из базы данных бота
Создает красивый и понятный отчет для админа
"""
import sqlite3
import os
import sys
from datetime import datetime, timezone
import pytz
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import ColorScaleRule

# Московское время
MoscowTz = pytz.timezone('Europe/Moscow')

# Путь к базе данных
DB_PATH = os.getenv("DB_PATH", "bot.db")

# Цвета для оформления
HEADER_FILL = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(bold=True, size=14)
BORDER = Border(
    left=Side(style='thin'),
    right=Side(style='thin'),
    top=Side(style='thin'),
    bottom=Side(style='thin')
)

def format_datetime(dt_str):
    """Форматирует дату в читаемый вид в МСК времени"""
    if not dt_str:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        # Если нет timezone, считаем что UTC
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        # Конвертируем в МСК
        moscow_dt = dt.astimezone(MoscowTz)
        return moscow_dt.strftime("%d.%m.%Y %H:%M:%S МСК")
    except:
        return dt_str

def format_status(status):
    """Форматирует статус платежа с цветом"""
    status_map = {
        "succeeded": "✅ Успешно",
        "pending": "⏳ Ожидает",
        "canceled": "❌ Отменен",
        "expired": "⏰ Истек"
    }
    return status_map.get(status, status)

def create_users_sheet(wb, conn):
    """Создает лист с пользователями"""
    ws = wb.active
    ws.title = "Пользователи"
    
    # Заголовок
    ws['A1'] = "СПИСОК ПОЛЬЗОВАТЕЛЕЙ"
    ws['A1'].font = TITLE_FONT
    ws.merge_cells('A1:H1')
    
    # Заголовки колонок
    headers = [
        "ID Telegram", "Username", "Дата регистрации", "Количество платежей",
        "Доступ с", "Доступ до", "Статус платежа", "Статус на канале"
    ]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = BORDER
    
    # Получаем данные с дополнительной информацией
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            u.telegram_id, 
            u.username, 
            u.created_at,
            COUNT(DISTINCT p.id) as payment_count,
            s.starts_at,
            s.expires_at,
            (SELECT status FROM payments WHERE telegram_id = u.telegram_id ORDER BY id DESC LIMIT 1) as last_payment_status,
            au.approved_at,
            (SELECT revoked FROM invite_links WHERE telegram_user_id = u.telegram_id ORDER BY created_at DESC LIMIT 1) as last_link_revoked
        FROM users u
        LEFT JOIN payments p ON u.telegram_id = p.telegram_id
        LEFT JOIN subscriptions s ON u.telegram_id = s.telegram_id
        LEFT JOIN approved_users au ON u.telegram_id = au.telegram_user_id
        GROUP BY u.telegram_id
        ORDER BY u.created_at DESC
    """)
    
    row = 4
    now = datetime.now(timezone.utc)
    for record in cur.fetchall():
        telegram_id, username, created_at, payment_count, starts_at, expires_at, last_payment_status, approved_at, last_link_revoked = record
        
        ws.cell(row=row, column=1, value=telegram_id).border = BORDER
        ws.cell(row=row, column=2, value=username or "—").border = BORDER
        ws.cell(row=row, column=3, value=format_datetime(created_at)).border = BORDER
        ws.cell(row=row, column=4, value=payment_count).border = BORDER
        
        # Период доступа
        ws.cell(row=row, column=5, value=format_datetime(starts_at)).border = BORDER
        expires_cell = ws.cell(row=row, column=6, value=format_datetime(expires_at))
        expires_cell.border = BORDER
        
        # Статус платежа
        status_cell = ws.cell(row=row, column=7, value=format_status(last_payment_status) if last_payment_status else "—")
        status_cell.border = BORDER
        if last_payment_status == "succeeded":
            status_cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        elif last_payment_status == "pending":
            status_cell.fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
        elif last_payment_status in ["canceled", "expired"]:
            status_cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
        
        # Статус на канале
        channel_status = "—"
        if approved_at:
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00')) if expires_at else None
                if expires_dt and expires_dt.tzinfo is None:
                    expires_dt = pytz.utc.localize(expires_dt)
                
                if expires_dt and expires_dt > now:
                    # Подписка активна
                    channel_status = f"✅ Добавлен: {format_datetime(approved_at)}"
                elif last_link_revoked and expires_dt:
                    # Забанен (ссылка отозвана и подписка истекла)
                    channel_status = f"❌ Забанен (примерно): {format_datetime(expires_at)}"
                else:
                    # Был добавлен, но статус неясен
                    channel_status = f"ℹ️ Добавлен: {format_datetime(approved_at)}"
            except:
                channel_status = f"ℹ️ Добавлен: {format_datetime(approved_at)}"
        
        channel_status_cell = ws.cell(row=row, column=8, value=channel_status)
        channel_status_cell.border = BORDER
        if "✅" in channel_status:
            channel_status_cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        elif "❌" in channel_status:
            channel_status_cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
        
        row += 1
    
    # Настройка ширины колонок
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 25
    ws.column_dimensions['C'].width = 22
    ws.column_dimensions['D'].width = 20
    ws.column_dimensions['E'].width = 22
    ws.column_dimensions['F'].width = 22
    ws.column_dimensions['G'].width = 18
    ws.column_dimensions['H'].width = 35

def create_payments_sheet(wb, conn):
    """Создает лист с платежами"""
    ws = wb.create_sheet("Платежи")
    
    # Заголовок
    ws['A1'] = "ИСТОРИЯ ПЛАТЕЖЕЙ"
    ws['A1'].font = TITLE_FONT
    ws.merge_cells('A1:H1')
    
    # Заголовки колонок
    headers = [
        "ID Платежа", "ID Пользователя", "Username", "Статус", 
        "Сумма (руб)", "Дата создания", "Дата обработки", "Ссылка создана"
    ]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = BORDER
    
    # Получаем данные
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            p.payment_id,
            p.telegram_id,
            u.username,
            p.status,
            p.created_at,
            pp.processed_at,
            il.created_at as link_created
        FROM payments p
        LEFT JOIN users u ON p.telegram_id = u.telegram_id
        LEFT JOIN processed_payments pp ON p.payment_id = pp.payment_id
        LEFT JOIN invite_links il ON p.payment_id = il.payment_id
        ORDER BY p.created_at DESC
    """)
    
    row = 4
    for record in cur.fetchall():
        payment_id, telegram_id, username, status, created_at, processed_at, link_created = record
        ws.cell(row=row, column=1, value=payment_id).border = BORDER
        ws.cell(row=row, column=2, value=telegram_id).border = BORDER
        ws.cell(row=row, column=3, value=username or "—").border = BORDER
        status_cell = ws.cell(row=row, column=4, value=format_status(status))
        status_cell.border = BORDER
        ws.cell(row=row, column=5, value="1.00").border = BORDER  # Сумма из конфига
        ws.cell(row=row, column=6, value=format_datetime(created_at)).border = BORDER
        ws.cell(row=row, column=7, value=format_datetime(processed_at)).border = BORDER
        ws.cell(row=row, column=8, value="Да" if link_created else "Нет").border = BORDER
        
        # Цветовая индикация статуса
        if status == "succeeded":
            status_cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        elif status == "pending":
            status_cell.fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
        elif status in ["canceled", "expired"]:
            status_cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
        
        row += 1
    
    # Настройка ширины колонок
    ws.column_dimensions['A'].width = 40
    ws.column_dimensions['B'].width = 18
    ws.column_dimensions['C'].width = 25
    ws.column_dimensions['D'].width = 15
    ws.column_dimensions['E'].width = 12
    ws.column_dimensions['F'].width = 20
    ws.column_dimensions['G'].width = 20
    ws.column_dimensions['H'].width = 15

def create_subscriptions_sheet(wb, conn):
    """Создает лист с подписками"""
    ws = wb.create_sheet("Подписки")
    
    # Заголовок
    ws['A1'] = "АКТИВНЫЕ И ИСТЕКШИЕ ПОДПИСКИ"
    ws['A1'].font = TITLE_FONT
    ws.merge_cells('A1:H1')
    
    # Заголовки колонок
    headers = [
        "ID Пользователя", "Username", "Начало доступа", "Окончание доступа", 
        "Статус", "Автопродление", "Сохранена карта", "Уведомление отправлено"
    ]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = BORDER
    
    # Получаем данные
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            s.telegram_id,
            u.username,
            s.starts_at,
            s.expires_at,
            s.auto_renewal_enabled,
            s.saved_payment_method_id,
            s.subscription_expired_notified
        FROM subscriptions s
        LEFT JOIN users u ON s.telegram_id = u.telegram_id
        ORDER BY s.expires_at DESC
    """)
    
    row = 4
    now = datetime.now(timezone.utc)
    for record in cur.fetchall():
        telegram_id, username, starts_at, expires_at, auto_renewal, saved_card, notified = record
        ws.cell(row=row, column=1, value=telegram_id).border = BORDER
        ws.cell(row=row, column=2, value=username or "—").border = BORDER
        ws.cell(row=row, column=3, value=format_datetime(starts_at)).border = BORDER
        expires_cell = ws.cell(row=row, column=4, value=format_datetime(expires_at))
        expires_cell.border = BORDER
        
        # Определяем статус
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            if expires_dt > now:
                status = "✅ Активна"
                status_cell = ws.cell(row=row, column=5, value=status)
                status_cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            else:
                status = "⏰ Истекла"
                status_cell = ws.cell(row=row, column=5, value=status)
                status_cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
        except:
            status_cell = ws.cell(row=row, column=5, value="—")
        status_cell.border = BORDER
        
        ws.cell(row=row, column=6, value="Да" if auto_renewal else "Нет").border = BORDER
        ws.cell(row=row, column=7, value="Да" if saved_card else "Нет").border = BORDER
        ws.cell(row=row, column=8, value="Да" if notified else "Нет").border = BORDER
        
        row += 1
    
    # Настройка ширины колонок
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 25
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['D'].width = 20
    ws.column_dimensions['E'].width = 15
    ws.column_dimensions['F'].width = 15
    ws.column_dimensions['G'].width = 15
    ws.column_dimensions['H'].width = 20

def create_invite_links_sheet(wb, conn):
    """Создает лист с пригласительными ссылками"""
    ws = wb.create_sheet("Пригласительные ссылки")
    
    # Заголовок
    ws['A1'] = "ИСТОРИЯ ПРИГЛАСИТЕЛЬНЫХ ССЫЛОК"
    ws['A1'].font = TITLE_FONT
    ws.merge_cells('A1:F1')
    
    # Заголовки колонок
    headers = [
        "Ссылка", "ID Пользователя", "Username", "ID Платежа", 
        "Дата создания", "Статус"
    ]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = BORDER
    
    # Получаем данные
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            il.invite_link,
            il.telegram_user_id,
            u.username,
            il.payment_id,
            il.created_at,
            il.revoked
        FROM invite_links il
        LEFT JOIN users u ON il.telegram_user_id = u.telegram_id
        ORDER BY il.created_at DESC
    """)
    
    row = 4
    for record in cur.fetchall():
        invite_link, telegram_id, username, payment_id, created_at, revoked = record
        ws.cell(row=row, column=1, value=invite_link).border = BORDER
        ws.cell(row=row, column=2, value=telegram_id).border = BORDER
        ws.cell(row=row, column=3, value=username or "—").border = BORDER
        ws.cell(row=row, column=4, value=payment_id).border = BORDER
        ws.cell(row=row, column=5, value=format_datetime(created_at)).border = BORDER
        status_cell = ws.cell(row=row, column=6, value="Отозвана" if revoked else "Активна")
        status_cell.border = BORDER
        if revoked:
            status_cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
        else:
            status_cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        row += 1
    
    # Настройка ширины колонок
    ws.column_dimensions['A'].width = 45
    ws.column_dimensions['B'].width = 18
    ws.column_dimensions['C'].width = 25
    ws.column_dimensions['D'].width = 40
    ws.column_dimensions['E'].width = 20
    ws.column_dimensions['F'].width = 15

def create_summary_sheet(wb, conn):
    """Создает сводный лист со статистикой"""
    ws = wb.create_sheet("Сводка", 0)  # Первый лист
    
    # Заголовок
    ws['A1'] = "СВОДНАЯ СТАТИСТИКА"
    ws['A1'].font = Font(bold=True, size=16)
    ws.merge_cells('A1:B1')
    
    cur = conn.cursor()
    
    # Общая статистика
    row = 3
    ws.cell(row=row, column=1, value="Параметр").font = Font(bold=True)
    ws.cell(row=row, column=2, value="Значение").font = Font(bold=True)
    row += 1
    
    # Количество пользователей
    cur.execute("SELECT COUNT(*) FROM users")
    user_count = cur.fetchone()[0]
    ws.cell(row=row, column=1, value="Всего пользователей:")
    ws.cell(row=row, column=2, value=user_count)
    row += 1
    
    # Количество активных подписок
    cur.execute("""
        SELECT COUNT(*) FROM subscriptions 
        WHERE expires_at > datetime('now', 'utc')
    """)
    active_subs = cur.fetchone()[0]
    ws.cell(row=row, column=1, value="Активных подписок:")
    ws.cell(row=row, column=2, value=active_subs)
    row += 1
    
    # Количество успешных платежей
    cur.execute("SELECT COUNT(*) FROM payments WHERE status = 'succeeded'")
    success_payments = cur.fetchone()[0]
    ws.cell(row=row, column=1, value="Успешных платежей:")
    ws.cell(row=row, column=2, value=success_payments)
    row += 1
    
    # Общая сумма (успешные платежи * 1 руб)
    total_amount = success_payments * 1.00
    ws.cell(row=row, column=1, value="Общая сумма (руб):")
    ws.cell(row=row, column=2, value=total_amount)
    row += 1
    
    # Платежи в ожидании
    cur.execute("SELECT COUNT(*) FROM payments WHERE status = 'pending'")
    pending_payments = cur.fetchone()[0]
    ws.cell(row=row, column=1, value="Платежей в ожидании:")
    ws.cell(row=row, column=2, value=pending_payments)
    row += 1
    
    # Отмененные платежи
    cur.execute("SELECT COUNT(*) FROM payments WHERE status = 'canceled'")
    canceled_payments = cur.fetchone()[0]
    ws.cell(row=row, column=1, value="Отмененных платежей:")
    ws.cell(row=row, column=2, value=canceled_payments)
    row += 1
    
    # Пользователи с автопродлением
    cur.execute("SELECT COUNT(*) FROM subscriptions WHERE auto_renewal_enabled = 1")
    auto_renewal_count = cur.fetchone()[0]
    ws.cell(row=row, column=1, value="Пользователей с автопродлением:")
    ws.cell(row=row, column=2, value=auto_renewal_count)
    row += 1
    
    # Дата генерации отчета
    row += 1
    ws.cell(row=row, column=1, value="Дата генерации отчета:")
    ws.cell(row=row, column=2, value=datetime.now().strftime("%d.%m.%Y %H:%M:%S"))
    
    # Настройка ширины колонок
    ws.column_dimensions['A'].width = 35
    ws.column_dimensions['B'].width = 20

def main():
    """Главная функция"""
    if not os.path.exists(DB_PATH):
        print(f"❌ Ошибка: База данных не найдена: {DB_PATH}")
        sys.exit(1)
    
    print(f"📊 Генерация Excel отчета из базы данных: {DB_PATH}")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        
        # Создаем рабочую книгу
        wb = Workbook()
        
        # Создаем листы
        print("  ✓ Создание листа 'Сводка'...")
        create_summary_sheet(wb, conn)
        
        print("  ✓ Создание листа 'Пользователи'...")
        create_users_sheet(wb, conn)
        
        print("  ✓ Создание листа 'Платежи'...")
        create_payments_sheet(wb, conn)
        
        print("  ✓ Создание листа 'Подписки'...")
        create_subscriptions_sheet(wb, conn)
        
        print("  ✓ Создание листа 'Пригласительные ссылки'...")
        create_invite_links_sheet(wb, conn)
        
        # Сохраняем файл
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"bot_report_{timestamp}.xlsx"
        wb.save(filename)
        
        conn.close()
        
        print(f"\n✅ Отчет успешно создан: {filename}")
        print(f"   Полный путь: {os.path.abspath(filename)}")
        
    except Exception as e:
        print(f"❌ Ошибка при создании отчета: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

