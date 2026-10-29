from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from jobagent.db import (
    SCHEMA_VERSION,
    already_applied,
    now,
    open_db,
    sent_today,
    upsert_vacancy,
)


@pytest.fixture
def conn(tmp_path):
    connection = open_db(tmp_path / "test.sqlite3")
    yield connection
    connection.close()


def test_схема_создаётся_и_версионируется(conn):
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert row["value"] == str(SCHEMA_VERSION)


def test_повторная_инициализация_безопасна(tmp_path):
    path = tmp_path / "twice.sqlite3"
    open_db(path).close()
    conn = open_db(path)
    conn.close()


def test_вакансия_не_дублируется(conn):
    first = upsert_vacancy(
        conn, platform="hh", external_id="123", title="Разработчик с ИИ"
    )
    second = upsert_vacancy(
        conn, platform="hh", external_id="123", title="Разработчик с ИИ (обновлено)"
    )
    assert first == second
    rows = conn.execute("SELECT title FROM vacancies").fetchall()
    assert len(rows) == 1
    assert rows[0]["title"].endswith("(обновлено)")


def _application(conn, vacancy_id: int, status: str, sent_at: str | None = None):
    conn.execute(
        "INSERT INTO applications(vacancy_id, platform, mode, status, created_at, sent_at)"
        " VALUES (?, 'superjob', 'auto', ?, ?, ?)",
        (vacancy_id, status, now(), sent_at),
    )
    conn.commit()


def test_повторный_отклик_невозможен(conn):
    vacancy = upsert_vacancy(conn, platform="superjob", external_id="1", title="Х")
    _application(conn, vacancy, "sent", now())
    assert already_applied(conn, vacancy)
    with pytest.raises(sqlite3.IntegrityError):
        _application(conn, vacancy, "sent", now())


def test_черновики_повторам_не_мешают(conn):
    vacancy = upsert_vacancy(conn, platform="superjob", external_id="2", title="Y")
    _application(conn, vacancy, "draft")
    _application(conn, vacancy, "draft")
    assert not already_applied(conn, vacancy)


def test_дневной_счётчик_считает_только_отправленные(conn):
    today = datetime.now(timezone.utc).date()
    for i in range(3):
        vacancy = upsert_vacancy(
            conn, platform="superjob", external_id=f"s{i}", title="Z"
        )
        _application(conn, vacancy, "sent", now())
    draft = upsert_vacancy(conn, platform="superjob", external_id="d", title="D")
    _application(conn, draft, "draft")
    assert sent_today(conn, today) == 3
