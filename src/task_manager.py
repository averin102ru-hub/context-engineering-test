"""
Task Manager — простой веб-сервис для управления задачами.
Используется как тестовый проект для эксперимента с AI code review.

Рефакторинг: унифицирован формат хранения паролей, добавлена
поддержка мягкого удаления задач, расширена валидация.
"""

import sqlite3
import hashlib
import os
import json
import re
from datetime import datetime, timedelta
from typing import Optional


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
            updated_at TEXT DEFAULT (datetime('now')),
            deleted_at TEXT DEFAULT NULL
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

# OUTSIDE-DIFF BUG 1: Изменён формат хранения пароля.
# Раньше: "salt:hash". Теперь: "hash$salt" (якобы "унификация").
# Функция hash_password и create_user обновлены, но authenticate() в diff НЕ менялась —
# она по-прежнему делает stored.split(":"), что сломает все новые аккаунты.
def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Хеширует пароль с солью (SHA-256 + salt). Формат v2."""
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
    # CHANGED: новый формат хранения — "hash$salt" вместо "salt:hash"
    stored = f"{hashed}${salt}"

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
    # BUG: по-прежнему split(":"), но create_user теперь пишет "$"
    # Для новых пользователей — crash (ValueError: not enough values to unpack)
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
    query = "SELECT * FROM tasks WHERE deleted_at IS NULL"
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


# OUTSIDE-DIFF BUG 2: delete_task заменена на soft delete.
# Проставляет deleted_at, но НЕ каскадно удаляет comments и attachments.
# Старый код делал DELETE — и cascade работал. Теперь записи-сироты
# остаются в таблице comments/attachments, ссылаясь на "удалённую" задачу.
# Плюс: get_task не фильтрует по deleted_at — "удалённые" задачи видны через get_task.
def delete_task(task_id: int, user_id: int) -> bool:
    """Мягкое удаление задачи (soft delete)."""
    conn = get_connection()
    task = conn.execute("SELECT creator_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        conn.close()
        return False

    user = conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if task["creator_id"] != user_id and not (user and user["is_admin"]):
        conn.close()
        raise PermissionError("Only the creator or admin can delete tasks")

    # Soft delete вместо hard delete
    conn.execute(
        "UPDATE tasks SET deleted_at = ? WHERE id = ?",
        (datetime.now().isoformat(), task_id)
    )
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

# OUTSIDE-DIFF BUG 3: generate_report НЕ менялась, но list_tasks теперь
# фильтрует deleted_at IS NULL. А generate_report использует прямой SQL
# без фильтра deleted_at — отчёт считает удалённые задачи,
# list_tasks их не показывает. Расхождение данных.
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


# ─── Task Archival (new) ───────────────────────────────────

# OUTSIDE-DIFF BUG 4: archive_old_tasks использует update_task,
# который фильтрует allowed fields — "status" есть, но "deleted_at" нет.
# Поэтому update_task молча проигнорирует deleted_at, и архивация
# никогда не пометит задачи удалёнными. Баг тихий — нет ошибки.
def archive_old_tasks(days: int = 90) -> int:
    """Архивирует задачи старше N дней (мягкое удаление)."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_connection()
    rows = conn.execute(
        "SELECT id FROM tasks WHERE created_at < ? AND status = 'done' AND deleted_at IS NULL",
        (cutoff,)
    ).fetchall()
    conn.close()

    count = 0
    for row in rows:
        # update_task не знает про deleted_at — оно не в allowed set
        success = update_task(row["id"], deleted_at=datetime.now().isoformat(), status="cancelled")
        if success:
            count += 1
    return count


# OUTSIDE-DIFF BUG 5: validate_task_data вызывает create_task с due_date,
# но передаёт дату в формате DD.MM.YYYY, а create_task ожидает YYYY-MM-DD.
# Функция validate_task_data выглядит корректной — ошибка в несовместимости форматов
# между двумя функциями. Ни одна из них не содержит бага изолированно.
def validate_and_create_task(data: dict, creator_id: int) -> int:
    """Валидирует данные и создаёт задачу."""
    title = data.get("title", "").strip()
    if not title:
        raise ValueError("Title is required")

    description = data.get("description", "")
    priority = int(data.get("priority", 0))

    due_date = data.get("due_date")
    if due_date:
        # Валидация: принимаем DD.MM.YYYY (европейский формат)
        try:
            parsed = datetime.strptime(due_date, "%d.%m.%Y")
            # Передаём в create_task как есть — но create_task ожидает YYYY-MM-DD
            due_date_str = due_date  # BUG: нужно parsed.strftime("%Y-%m-%d")
        except ValueError:
            raise ValueError("Due date must be in DD.MM.YYYY format")
    else:
        due_date_str = None

    return create_task(
        title=title,
        creator_id=creator_id,
        description=description,
        priority=priority,
        due_date=due_date_str,
    )


# OUTSIDE-DIFF BUG 6: get_task_summary использует get_task + get_comments.
# После soft delete: get_task возвращает "удалённую" задачу (нет фильтра deleted_at),
# get_comments возвращает комментарии к ней. Итого: можно получить summary
# задачи, которая "удалена" и не видна в list_tasks. Утечка данных.
def get_task_summary(task_id: int) -> Optional[dict]:
    """Получает краткую сводку по задаче с количеством комментариев."""
    task = get_task(task_id)
    if not task:
        return None

    comments = get_comments(task_id)

    return {
        "id": task["id"],
        "title": task["title"],
        "status": task["status"],
        "priority": task["priority"],
        "comment_count": len(comments),
        "last_comment": comments[-1]["body"] if comments else None,
    }


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
    summary = get_task_summary(tid)
    print(f"Task summary: {json.dumps(summary, indent=2)}")

    archived = archive_old_tasks(days=30)
    print(f"Archived {archived} old tasks")
