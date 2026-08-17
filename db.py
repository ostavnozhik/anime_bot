import sqlite3
import datetime

DB_NAME = "users.db"  # файл базы данных

def get_db():
    """Подключение к БД (автоматически создаёт файл, если его нет)"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # чтобы обращаться к колонкам по имени
    return conn

def init_db():
    """Создаёт таблицу users, если её нет"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

def add_user(user_id: int, username: str = None, first_name: str = None):
    """Добавляет пользователя, если его ещё нет"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, joined_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (user_id, username, first_name))
    conn.commit()
    conn.close()

def get_total_users() -> int:
    """Общее количество пользователей за всё время"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_monthly_users() -> int:
    """Количество пользователей за последние 30 дней"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COUNT(*) FROM users 
        WHERE joined_at >= datetime('now', '-30 days')
    ''')
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_weekly_users() -> int:
    """Количество пользователей за последние 7 дней"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COUNT(*) FROM users 
        WHERE joined_at >= datetime('now', '-7 days')
    ''')
    count = cursor.fetchone()[0]
    conn.close()
    return count
