"""SQLite-хранилище. Вся схема — в одном месте, миграции идемпотентные.

Правила:
- секретов в базе нет;
- для каждого отклика хранятся точные тексты резюме и письма, чтобы их можно
  было перечитать спустя месяцы;
- дубликат отправленного отклика на одну вакансию невозможен физически —
  частичный уникальный индекс `applications_sent_unique`.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vacancies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    url          TEXT,
    title        TEXT NOT NULL,
    company      TEXT,
    area         TEXT,
    salary_from  INTEGER,
    salary_to    INTEGER,
    currency     TEXT,
    schedule     TEXT,
    employment   TEXT,
    description  TEXT,
    published_at TEXT,
    found_at     TEXT NOT NULL,
    raw_json     TEXT,
    UNIQUE (platform, external_id)
);

CREATE TABLE IF NOT EXISTS scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id    INTEGER NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
    profile       TEXT NOT NULL,
    score         INTEGER,
    verdict       TEXT NOT NULL,           -- apply / skip / ask
    matched       TEXT,                    -- json: совпавшие требования
    gaps          TEXT,                    -- json: пробелы
    deal_breakers TEXT,                    -- json: стоп-факторы
    reasoning     TEXT,
    model         TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scores_vacancy ON scores(vacancy_id, profile);

CREATE TABLE IF NOT EXISTS applications (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id        INTEGER NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
    score_id          INTEGER REFERENCES scores(id) ON DELETE SET NULL,
    platform          TEXT NOT NULL,
    mode              TEXT NOT NULL,       -- dry_run / approve / auto
    status            TEXT NOT NULL,       -- draft / awaiting / sent / failed / skipped
    resume_path       TEXT,
    resume_text       TEXT,
    letter_text       TEXT,
    platform_response TEXT,
    error             TEXT,
    created_at        TEXT NOT NULL,
    sent_at           TEXT
);
-- запрет повторного отклика: только для реально отправленных
CREATE UNIQUE INDEX IF NOT EXISTS applications_sent_unique
    ON applications(vacancy_id) WHERE status = 'sent';
CREATE INDEX IF NOT EXISTS applications_sent_at ON applications(sent_at);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile         TEXT NOT NULL,
    platform        TEXT NOT NULL,
    taken_at        TEXT NOT NULL,
    vacancies_count INTEGER,
    salary_p25      INTEGER,
    salary_median   INTEGER,
    salary_p75      INTEGER,
    top_skills      TEXT,                  -- json
    raw_json        TEXT
);

CREATE TABLE IF NOT EXISTS interviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id     INTEGER NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
    application_id INTEGER REFERENCES applications(id) ON DELETE SET NULL,
    invited_at     TEXT,
    scheduled_at   TEXT,
    stage          TEXT,
    status         TEXT,
    prep_path      TEXT,
    notes          TEXT
);
"""


def now() -> str:
    """UTC в ISO. Показывать пользователю — уже в его часовом поясе."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def open_db(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    init_db(conn)
    return conn


def sent_today(conn: sqlite3.Connection, day: date | None = None) -> int:
    """Сколько откликов реально ушло за сутки — для дневного лимита."""
    day = day or datetime.now(timezone.utc).date()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM applications "
        "WHERE status = 'sent' AND date(sent_at) = ?",
        (day.isoformat(),),
    ).fetchone()
    return int(row["n"])


def already_applied(conn: sqlite3.Connection, vacancy_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM applications WHERE vacancy_id = ? AND status = 'sent'",
        (vacancy_id,),
    ).fetchone()
    return row is not None


def upsert_vacancy(conn: sqlite3.Connection, **fields: object) -> int:
    """Записывает вакансию, обновляя уже известную. Возвращает её id в базе."""
    fields.setdefault("found_at", now())
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(
        f"{c} = excluded.{c}"
        for c in fields
        if c not in ("platform", "external_id", "found_at")
    )
    sql = (
        f"INSERT INTO vacancies ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(platform, external_id) DO UPDATE SET {updates}"
        if updates
        else f"INSERT OR IGNORE INTO vacancies ({columns}) VALUES ({placeholders})"
    )
    conn.execute(sql, tuple(fields.values()))
    row = conn.execute(
        "SELECT id FROM vacancies WHERE platform = ? AND external_id = ?",
        (fields["platform"], fields["external_id"]),
    ).fetchone()
    conn.commit()
    return int(row["id"])
