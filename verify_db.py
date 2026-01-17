#!/usr/bin/env python3
import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/bot_telegram/bot.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

tables = ["users", "subscriptions", "payments", "approved_users", "invite_links", "processed_payments"]
for table in tables:
    try:
        count = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {count}")
    except:
        print(f"{table}: таблица не существует")

conn.close()

