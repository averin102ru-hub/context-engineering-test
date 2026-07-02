"""Тесты для task_manager."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from task_manager import (
    init_db, create_user, authenticate, create_task,
    get_task, list_tasks, update_task, delete_task,
    add_comment, get_comments, generate_report, DB_PATH
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Каждый тест получает чистую БД."""
    db = str(tmp_path / "test.db")
    monkeypatch.setattr("task_manager.DB_PATH", db)
    init_db()
    yield db


class TestAuth:
    def test_create_user(self):
        uid = create_user("testuser", "password123")
        assert uid > 0

    def test_duplicate_user(self):
        create_user("testuser", "password123")
        with pytest.raises(ValueError, match="already exists"):
            create_user("testuser", "password456")

    def test_short_password(self):
        with pytest.raises(ValueError, match="at least 8"):
            create_user("testuser", "short")

    def test_invalid_username(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            create_user("bad user!", "password123")

    def test_authenticate_success(self):
        create_user("testuser", "password123")
        user = authenticate("testuser", "password123")
        assert user is not None
        assert user["username"] == "testuser"

    def test_authenticate_wrong_password(self):
        create_user("testuser", "password123")
        user = authenticate("testuser", "wrongpass")
        assert user is None

    def test_authenticate_nonexistent(self):
        user = authenticate("nobody", "password123")
        assert user is None


class TestTasks:
    def test_create_task(self):
        uid = create_user("testuser", "password123")
        tid = create_task("Test task", uid)
        assert tid > 0

    def test_get_task(self):
        uid = create_user("testuser", "password123")
        tid = create_task("Test task", uid, "Description", priority=3)
        task = get_task(tid)
        assert task["title"] == "Test task"
        assert task["priority"] == 3

    def test_list_tasks(self):
        uid = create_user("testuser", "password123")
        create_task("Task 1", uid)
        create_task("Task 2", uid)
        tasks = list_tasks()
        assert len(tasks) == 2

    def test_update_task(self):
        uid = create_user("testuser", "password123")
        tid = create_task("Test task", uid)
        result = update_task(tid, status="in_progress")
        assert result is True
        task = get_task(tid)
        assert task["status"] == "in_progress"

    def test_delete_task(self):
        uid = create_user("testuser", "password123")
        tid = create_task("Test task", uid)
        result = delete_task(tid, uid)
        assert result is True
        assert get_task(tid) is None

    def test_empty_title(self):
        uid = create_user("testuser", "password123")
        with pytest.raises(ValueError, match="empty"):
            create_task("", uid)


class TestComments:
    def test_add_comment(self):
        uid = create_user("testuser", "password123")
        tid = create_task("Test task", uid)
        cid = add_comment(tid, uid, "Test comment")
        assert cid > 0

    def test_get_comments(self):
        uid = create_user("testuser", "password123")
        tid = create_task("Test task", uid)
        add_comment(tid, uid, "Comment 1")
        add_comment(tid, uid, "Comment 2")
        comments = get_comments(tid)
        assert len(comments) == 2


class TestReports:
    def test_generate_report(self):
        uid = create_user("testuser", "password123")
        create_task("Task 1", uid)
        create_task("Task 2", uid)
        report = generate_report()
        assert report["total"] == 2
        assert "open" in report["by_status"]
