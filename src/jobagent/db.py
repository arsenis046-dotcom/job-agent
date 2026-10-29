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

SCHEMA_VERSION = 2

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
    last_seen    TEXT,
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


def _migrate(conn: sqlite3.Connection) -> None:
    """Догоняющие правки для баз, созданных прежними версиями схемы."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(vacancies)")}
    if "last_seen" not in columns:  # схема v1 -> v2
        conn.execute("ALTER TABLE vacancies ADD COLUMN last_seen TEXT")
        conn.execute("UPDATE vacancies SET last_seen = found_at WHERE last_seen IS NULL")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
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
    # каждая встреча вакансии в выдаче обновляет отметку: по ней видно,
    # что вакансия ещё жива (п. 3.11 Условий hh.ru требует убирать архивные)
    fields["last_seen"] = now()
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


def unscored(
    conn: sqlite3.Connection, profile: str, *, again: bool = False
) -> list[sqlite3.Row]:
    """Вакансии к оценке под этот профиль.

    Обычно — только ещё не оценённые. С `again=True` берутся все: это нужно,
    когда поменялся профиль поиска или движок оценки. Прежние оценки не
    затираются, они остаются в истории, а в отчёт идёт последняя.
    """
    if again:
        return conn.execute("SELECT * FROM vacancies ORDER BY found_at DESC").fetchall()
    return conn.execute(
        "SELECT v.* FROM vacancies v "
        "WHERE NOT EXISTS (SELECT 1 FROM scores s "
        "                  WHERE s.vacancy_id = v.id AND s.profile = ?) "
        "ORDER BY v.found_at DESC",
        (profile,),
    ).fetchall()


def score_history(conn: sqlite3.Connection, profile: str) -> list[sqlite3.Row]:
    """Последняя оценка каждым движком по каждой вакансии — для сравнения."""
    return conn.execute(
        "SELECT v.id, v.title, v.company, s.model, s.score, s.verdict, s.reasoning "
        "FROM vacancies v JOIN scores s ON s.vacancy_id = v.id "
        "WHERE s.profile = ? AND s.id = ("
        "    SELECT id FROM scores "
        "    WHERE vacancy_id = v.id AND profile = ? AND model IS s.model "
        "    ORDER BY created_at DESC, id DESC LIMIT 1) "
        "ORDER BY v.id",
        (profile, profile),
    ).fetchall()


def save_score(
    conn: sqlite3.Connection,
    vacancy_id: int,
    profile: str,
    *,
    score: int | None,
    verdict: str,
    matched: str | None = None,
    gaps: str | None = None,
    deal_breakers: str | None = None,
    reasoning: str | None = None,
    model: str | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO scores (vacancy_id, profile, score, verdict, matched, gaps, "
        "deal_breakers, reasoning, model, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            vacancy_id,
            profile,
            score,
            verdict,
            matched,
            gaps,
            deal_breakers,
            reasoning,
            model,
            now(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def scored(conn: sqlite3.Connection, profile: str) -> list[sqlite3.Row]:
    """Вакансии с последней оценкой — для отчёта. Переоценки не ломают порядок."""
    return conn.execute(
        "SELECT v.*, s.score, s.verdict, s.reasoning, s.matched, s.gaps, "
        "       s.deal_breakers, s.model, s.created_at AS scored_at, "
        "       (SELECT 1 FROM applications a "
        "        WHERE a.vacancy_id = v.id AND a.status = 'sent') AS applied "
        "FROM vacancies v "
        "JOIN scores s ON s.id = ("
        "    SELECT id FROM scores WHERE vacancy_id = v.id AND profile = ?"
        "    ORDER BY created_at DESC, id DESC LIMIT 1) "
        "ORDER BY s.score IS NULL, s.score DESC, v.found_at DESC",
        (profile,),
    ).fetchall()


def stale(conn: sqlite3.Connection, days: int) -> list[sqlite3.Row]:
    """Вакансии, не попадавшиеся в выдаче дольше N суток: скорее всего, в архиве."""
    return conn.execute(
        "SELECT * FROM vacancies "
        "WHERE COALESCE(last_seen, found_at) < datetime('now', ?) "
        "ORDER BY COALESCE(last_seen, found_at)",
        (f"-{int(days)} days",),
    ).fetchall()


def delete_vacancies(conn: sqlite3.Connection, ids: list[int]) -> int:
    """Удаляет вакансии вместе с оценками (каскадом). Отклики тоже уйдут,
    поэтому вызывающий обязан сначала исключить те, на которые откликались."""
    if not ids:
        return 0
    marks = ", ".join("?" for _ in ids)
    cursor = conn.execute(f"DELETE FROM vacancies WHERE id IN ({marks})", ids)
    conn.commit()
    return cursor.rowcount
