"""
Task Manager — простой веб-сервис для управления задачами.
Используется как тестовый проект для эксперимента с AI code review.

Обновление: добавлен экспорт пользователей и поиск задач.
"""

import sqlite3
import hashlib
import os
import json
import re
import pickle
from datetime import datetime, timedelta
from typing import Optional
from threading import Thread


# ─── Database ───────────────────────────────────────────────

DB_PATH = os.environ.get("TASKMAN_DB", "tasks.db")


def get_connection() -> sqlite3.Connection:
    """Создаёт подключение к SQLite с row_factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Инициализация схемы БД."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            is_admin INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'open' CHECK(status IN ('open','in_progress','done','cancelled')),
            priority INTEGER DEFAULT 0 CHECK(priority BETWEEN 0 AND 5),
            assignee_id INTEGER REFERENCES users(id),
            creator_id INTEGER NOT NULL REFERENCES users(id),
            due_date TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            body TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            filepath TEXT NOT NULL,
            uploaded_by INTEGER NOT NULL REFERENCES users(id),
            uploaded_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


# ─── Auth ───────────────────────────────────────────────────

def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Хеширует пароль с солью (SHA-256 + salt)."""
    if salt is None:
        salt = os.urandom(16).hex()
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return hashed, salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Проверяет пароль по хешу и соли."""
    computed, _ = hash_password(password, salt)
    return computed == stored_hash


def create_user(username: str, password: str, email: str = None) -> int:
    """Создаёт пользователя, возвращает ID."""
    if not re.match(r'^[a-zA-Z0-9_]{3,30}$', username):
        raise ValueError("Username must be 3-30 alphanumeric characters")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")

    hashed, salt = hash_password(password)
    stored = f"{salt}:{hashed}"

    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, email) VALUES (?, ?, ?)",
            (username, stored, email)
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"Username '{username}' already exists")
    finally:
        conn.close()


def authenticate(username: str, password: str) -> Optional[dict]:
    """Аутентификация: возвращает dict пользователя или None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()

    if row is None:
        return None

    stored = row["password_hash"]
    salt, hashed = stored.split(":")
    if verify_password(password, hashed, salt):
        return dict(row)
    return None


# ─── Tasks CRUD ─────────────────────────────────────────────

def create_task(title: str, creator_id: int, description: str = "",
                priority: int = 0, assignee_id: int = None,
                due_date: str = None) -> int:
    """Создаёт задачу, возвращает ID."""
    if not title or not title.strip():
        raise ValueError("Title cannot be empty")
    if priority < 0 or priority > 5:
        raise ValueError("Priority must be between 0 and 5")
    if due_date:
        try:
            parsed = datetime.strptime(due_date, "%Y-%m-%d")
            if parsed.date() < datetime.now().date():
                raise ValueError("Due date cannot be in the past")
        except ValueError as e:
            if "does not match" in str(e):
                raise ValueError("Due date must be in YYYY-MM-DD format")
            raise

    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO tasks (title, description, priority, assignee_id, creator_id, due_date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (title.strip(), description.strip(), priority, assignee_id, creator_id, due_date)
    )
    conn.commit()
    task_id = cursor.lastrowid
    conn.close()
    return task_id


def get_task(task_id: int) -> Optional[dict]:
    """Получает задачу по ID."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_tasks(status: str = None, assignee_id: int = None,
               limit: int = 50, offset: int = 0) -> list[dict]:
    """Список задач с фильтрацией."""
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []

    if status:
        query += " AND status = ?"
        params.append(status)
    if assignee_id is not None:
        query += " AND assignee_id = ?"
        params.append(assignee_id)

    query += " ORDER BY priority DESC, created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = get_connection()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_task(task_id: int, **kwargs) -> bool:
    """Обновляет поля задачи."""
    allowed = {"title", "description", "status", "priority", "assignee_id", "due_date"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}

    if not fields:
        return False

    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]

    conn = get_connection()
    result = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
    conn.commit()
    updated = result.rowcount > 0
    conn.close()
    return updated


def delete_task(task_id: int, user_id: int) -> bool:
    """Удаляет задачу (только создатель или админ)."""
    conn = get_connection()
    task = conn.execute("SELECT creator_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        conn.close()
        return False

    user = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if task["creator_id"] != user_id and not (user and user["is_admin"]):
        conn.close()
        raise PermissionError("Only the creator or admin can delete tasks")

    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return True


# ─── Comments ───────────────────────────────────────────────

def add_comment(task_id: int, user_id: int, body: str) -> int:
    """Добавляет комментарий к задаче."""
    if not body or not body.strip():
        raise ValueError("Comment body cannot be empty")

    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")

    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO comments (task_id, user_id, body) VALUES (?, ?, ?)",
        (task_id, user_id, body.strip())
    )
    conn.commit()
    comment_id = cursor.lastrowid
    conn.close()
    return comment_id


def get_comments(task_id: int) -> list[dict]:
    """Получает все комментарии к задаче."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT c.*, u.username FROM comments c JOIN users u ON c.user_id = u.id "
        "WHERE c.task_id = ? ORDER BY c.created_at ASC",
        (task_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Attachments ────────────────────────────────────────────

UPLOAD_DIR = os.environ.get("TASKMAN_UPLOADS", "uploads")
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".png", ".jpg", ".csv", ".json", ".md"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def save_attachment(task_id: int, user_id: int, filename: str,
                    file_data: bytes) -> int:
    """Сохраняет файл-вложение к задаче."""
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type '{ext}' not allowed")

    if len(file_data) > MAX_FILE_SIZE:
        raise ValueError(f"File too large (max {MAX_FILE_SIZE // 1024 // 1024} MB)")

    # Безопасное имя файла
    safe_name = re.sub(r'[^\w\-.]', '_', filename)
    task_dir = os.path.join(UPLOAD_DIR, str(task_id))
    os.makedirs(task_dir, exist_ok=True)

    filepath = os.path.join(task_dir, safe_name)
    with open(filepath, "wb") as f:
        f.write(file_data)

    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO attachments (task_id, filename, filepath, uploaded_by) VALUES (?, ?, ?, ?)",
        (task_id, safe_name, filepath, user_id)
    )
    conn.commit()
    attachment_id = cursor.lastrowid
    conn.close()
    return attachment_id


# ─── Reports ────────────────────────────────────────────────

def generate_report(user_id: int = None) -> dict:
    """Генерирует отчёт по задачам."""
    conn = get_connection()

    if user_id:
        tasks = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks WHERE assignee_id = ? GROUP BY status",
            (user_id,)
        ).fetchall()
    else:
        tasks = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
        ).fetchall()

    report = {
        "generated_at": datetime.now().isoformat(),
        "by_status": {row["status"]: row["cnt"] for row in tasks},
        "total": sum(row["cnt"] for row in tasks),
    }

    # Overdue tasks
    overdue = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE due_date < ? AND status NOT IN ('done', 'cancelled')",
        (datetime.now().strftime("%Y-%m-%d"),)
    ).fetchone()
    report["overdue"] = overdue["cnt"]

    conn.close()
    return report


def export_tasks_json(filepath: str, status: str = None) -> int:
    """Экспортирует задачи в JSON-файл."""
    tasks = list_tasks(status=status, limit=10000)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2, default=str)
    return len(tasks)


# ═══════════════════════════════════════════════════════════
# НОВЫЙ КОД: экспорт пользователей, поиск, кеширование
# ═══════════════════════════════════════════════════════════


# ─── BUG L1-1: SQL Injection в поиске задач ─────────────────
def search_tasks(keyword: str) -> list[dict]:
    """Поиск задач по ключевому слову."""
    conn = get_connection()
    # Пользовательский ввод подставляется напрямую в SQL
    query = f"SELECT * FROM tasks WHERE title LIKE '%{keyword}%' OR description LIKE '%{keyword}%'"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── BUG L1-2: Пароль в логах ──────────────────────────────
def log_login_attempt(username: str, password: str, success: bool) -> None:
    """Логирует попытку входа."""
    status = "SUCCESS" if success else "FAILED"
    # Записываем пароль в лог-файл открытым текстом
    with open("auth.log", "a") as f:
        f.write(f"{datetime.now()} | {status} | user={username} | password={password}\n")


# ─── BUG L1-3: Хардкод секретного ключа ────────────────────
API_SECRET_KEY = "sk-prod-a8f3b2c1d4e5f6789012345678901234"
ADMIN_PASSWORD = "admin123!"


# ─── BUG L2-1: Экспорт всех пользователей с паролями ───────
def export_users(include_internal: bool = False) -> list[dict]:
    """Экспорт данных пользователей."""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    users = []
    for row in rows:
        user_data = {
            "id": row["id"],
            "username": row["username"],
            "email": row["email"],
            "password_hash": row["password_hash"],  # BUG: хеш пароля в экспорте
            "created_at": row["created_at"],
            "is_admin": row["is_admin"],
        }
        users.append(user_data)
    return users


# ─── BUG L2-2: Path Traversal при скачивании вложений ──────
def get_attachment_path(task_id: int, filename: str) -> str:
    """Возвращает путь к файлу вложения."""
    # Не проверяет ../  — можно выйти за пределы upload-директории
    return os.path.join(UPLOAD_DIR, str(task_id), filename)


# ─── BUG L2-3: Десериализация из непроверенного источника ───
def load_task_template(template_data: bytes) -> dict:
    """Загружает шаблон задачи из бинарных данных."""
    # pickle.loads от пользовательского ввода = RCE
    return pickle.loads(template_data)


# ─── BUG L2-4: Отсутствие проверки прав ────────────────────
def admin_delete_all_tasks() -> int:
    """Удаляет все задачи (админ-функция)."""
    # Нет проверки, является ли вызывающий админом
    conn = get_connection()
    result = conn.execute("DELETE FROM tasks")
    conn.commit()
    count = result.rowcount
    conn.close()
    return count


# ─── BUG L3-1: Race condition при обновлении счётчика ──────
task_view_counts = {}

def increment_view_count(task_id: int) -> int:
    """Увеличивает счётчик просмотров задачи."""
    # read-modify-write без блокировки — race condition
    current = task_view_counts.get(task_id, 0)
    current += 1
    task_view_counts[task_id] = current
    return current


# ─── BUG L3-2: Deadlock potential ──────────────────────────
import threading
lock_a = threading.Lock()
lock_b = threading.Lock()

def transfer_task_ownership(from_user: int, to_user: int) -> None:
    """Передаёт все задачи от одного пользователя другому."""
    with lock_a:
        # Имитация работы
        conn = get_connection()
        tasks = conn.execute(
            "SELECT id FROM tasks WHERE assignee_id = ?", (from_user,)
        ).fetchall()
        with lock_b:
            for task in tasks:
                conn.execute(
                    "UPDATE tasks SET assignee_id = ? WHERE id = ?",
                    (to_user, task["id"])
                )
            conn.commit()
        conn.close()

def reassign_and_notify(from_user: int, to_user: int) -> None:
    """Переназначает задачи и уведомляет."""
    # Захватывает lock_b, потом lock_a — обратный порядок = deadlock
    with lock_b:
        with lock_a:
            transfer_task_ownership(from_user, to_user)


# ─── BUG L3-3: Некорректная обработка exception из другой функции
def bulk_import_tasks(data: list[dict], creator_id: int) -> dict:
    """Массовый импорт задач из списка словарей."""
    results = {"success": 0, "failed": 0, "errors": []}
    for item in data:
        task_id = create_task(
            title=item.get("title", ""),
            creator_id=creator_id,
            description=item.get("description", ""),
            priority=item.get("priority", 0),
            due_date=item.get("due_date"),
        )
        results["success"] += 1
    # BUG: create_task может бросить ValueError (пустой title, плохой priority),
    # но тут нет try/except — одна ошибка убьёт весь импорт,
    # при этом часть задач уже создана (нет транзакции)
    return results


# ─── BUG L3-4: Memory leak в кеше без ограничения ──────────
_query_cache = {}

def cached_search(keyword: str) -> list[dict]:
    """Поиск с кешированием результатов."""
    # Каждый уникальный keyword добавляет запись в кеш навсегда
    # При большом количестве запросов — OOM
    if keyword not in _query_cache:
        _query_cache[keyword] = search_tasks(keyword)
    return _query_cache[keyword]


# ─── Async background export (использует баги выше) ────────
def async_export_users(filepath: str) -> None:
    """Асинхронный экспорт пользователей в файл."""
    def _export():
        users = export_users()  # включает password_hash
        with open(filepath, "w") as f:
            json.dump(users, f, indent=2, default=str)
    thread = Thread(target=_export)
    thread.start()
    # BUG: не ждём завершения потока, не обрабатываем ошибки


# ─── Main ───────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")

    # Демо
    uid = create_user("admin", "securepass123", "admin@example.com")
    print(f"Created user: id={uid}")

    tid = create_task("Fix login bug", uid, "Users can't login with special chars", priority=3)
    print(f"Created task: id={tid}")

    cid = add_comment(tid, uid, "Reproduced on Chrome 120")
    print(f"Added comment: id={cid}")

    report = generate_report()
    print(f"Report: {json.dumps(report, indent=2)}")

    # Новые функции
    results = search_tasks("login")
    print(f"Search results: {len(results)}")

    users = export_users()
    print(f"Exported {len(users)} users")
