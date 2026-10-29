from __future__ import annotations

import httpx
import pytest
import respx

from jobagent.config import Access
from jobagent.db import now, open_db, upsert_vacancy
from jobagent.pipeline import run_prune, run_score, run_search
from jobagent.profile import save_master_profile
from jobagent.reports.vacancies import write_report


def superjob_page(*objects, more: bool = False) -> httpx.Response:
    return httpx.Response(200, json={"objects": list(objects), "more": more})


def объявление(id_: int, profession: str, text: str, **kwargs) -> dict:
    return {
        "id": id_,
        "profession": profession,
        "candidat": text,
        "link": f"https://superjob.ru/vakansii/{id_}.html",
        "client": {"title": "ООО Пример"},
        "town": {"title": "Москва"},
        **kwargs,
    }


@pytest.fixture
def prepared(repo, config, sample_profile, monkeypatch):
    """Настоящий профиль в tmp плюс ключ площадки: конвейеру есть с чем работать."""
    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    save_master_profile(sample_profile, repo)
    return config


@respx.mock
def test_поиск_пишет_вакансии_в_базу(prepared):
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=superjob_page(
            объявление(1, "AI-разработчик", "Python, LLM, автоматизация"),
            объявление(2, "Senior разработчик", "Python, LLM"),
        )
    )
    outcome = run_search(prepared)

    keywords = len(prepared.profile.keywords)
    assert outcome.found == 2 * keywords
    assert outcome.new == 1, (
        "одна и та же вакансия по разным ключевым словам считается один раз, "
        "а вторая отсеяна по названию и в базу не попала"
    )
    assert outcome.skipped_by_title == keywords, "«Senior» отсеян по названию"
    assert not outcome.errors

    conn = open_db(prepared.db_path)
    try:
        rows = conn.execute("SELECT external_id FROM vacancies").fetchall()
    finally:
        conn.close()
    assert [r["external_id"] for r in rows] == ["1"]


@respx.mock
def test_ошибка_площадки_не_теряет_найденное(prepared):
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=httpx.Response(429, json={"error": {"code": 429}})
    )
    outcome = run_search(prepared)
    assert outcome.errors and "429" in outcome.errors[0]
    assert "слишком часто" in outcome.errors[0], "сообщение должно быть понятным"


@respx.mock
def test_повторный_поиск_не_плодит_дубликаты(prepared):
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=superjob_page(объявление(1, "AI-разработчик", "Python, LLM"))
    )
    run_search(prepared)
    second = run_search(prepared)
    assert second.new == 0

    conn = open_db(prepared.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) c FROM vacancies").fetchone()["c"] == 1
    finally:
        conn.close()


@respx.mock
def test_оценка_проходит_по_новым_вакансиям_один_раз(prepared):
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=superjob_page(
            объявление(1, "Разработчик с ИИ", "Python, LLM, git, автоматизация")
        )
    )
    run_search(prepared)

    first = run_score(prepared)
    assert first.scored == 1
    assert sum(first.by_verdict.values()) == 1

    assert run_score(prepared).scored == 0, "дважды одно и то же не оцениваем"

    # но по требованию — переоцениваем: сменился профиль или движок
    assert run_score(prepared, again=True).scored == 1

    conn = open_db(prepared.db_path)
    try:
        оценок = conn.execute("SELECT COUNT(*) c FROM scores").fetchone()["c"]
    finally:
        conn.close()
    assert оценок == 2, "прежняя оценка осталась в истории, а не затёрлась"


@respx.mock
def test_отчёт_собирается_и_содержит_вакансию(prepared):
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=superjob_page(
            объявление(
                1, "Разработчик с ИИ", "Python, LLM, автоматизация", payment_from=150000
            )
        )
    )
    run_search(prepared)
    run_score(prepared)

    md_path, csv_path = write_report(prepared)
    text = md_path.read_text(encoding="utf-8")
    assert "Разработчик с ИИ" in text
    assert "от 150 000" in text
    assert "## Чем считали" in text
    assert "Разработчик с ИИ" in csv_path.read_text(encoding="utf-8-sig")


def test_архивные_вакансии_удаляются(config):
    conn = open_db(config.db_path)
    try:
        свежая = upsert_vacancy(
            conn, platform="superjob", external_id="new", title="Свежая"
        )
        старая = upsert_vacancy(
            conn, platform="superjob", external_id="old", title="Старая"
        )
        conn.execute(
            "UPDATE vacancies SET last_seen = datetime('now', '-30 days') WHERE id = ?",
            (старая,),
        )
        conn.commit()
    finally:
        conn.close()

    outcome = run_prune(config, days=14)
    assert outcome.deleted == 1

    conn = open_db(config.db_path)
    try:
        rows = [r["id"] for r in conn.execute("SELECT id FROM vacancies")]
    finally:
        conn.close()
    assert rows == [свежая]


def test_вакансия_с_отправленным_откликом_не_удаляется(config):
    conn = open_db(config.db_path)
    try:
        vacancy_id = upsert_vacancy(
            conn, platform="superjob", external_id="old", title="Старая"
        )
        conn.execute(
            "UPDATE vacancies SET last_seen = datetime('now', '-30 days') WHERE id = ?",
            (vacancy_id,),
        )
        conn.execute(
            "INSERT INTO applications(vacancy_id, platform, mode, status, created_at, sent_at)"
            " VALUES (?, 'superjob', 'auto', 'sent', ?, ?)",
            (vacancy_id, now(), now()),
        )
        conn.commit()
    finally:
        conn.close()

    outcome = run_prune(config, days=14)
    assert (outcome.deleted, outcome.kept_applied) == (0, 1)


def test_старая_база_доживает_до_новой_схемы(config):
    """Схема v1 не знала last_seen: миграция должна добавить столбец."""
    import sqlite3

    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(config.db_path)
    raw.execute(
        "CREATE TABLE vacancies (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "platform TEXT NOT NULL, external_id TEXT NOT NULL, title TEXT NOT NULL, "
        "found_at TEXT NOT NULL, UNIQUE(platform, external_id))"
    )
    raw.execute(
        "INSERT INTO vacancies(platform, external_id, title, found_at) "
        "VALUES ('hh', '42', 'Старая запись', '2026-01-01T00:00:00+00:00')"
    )
    raw.commit()
    raw.close()

    conn = open_db(config.db_path)
    try:
        row = conn.execute("SELECT last_seen FROM vacancies").fetchone()
        assert row["last_seen"] == "2026-01-01T00:00:00+00:00", (
            "у старых записей last_seen берётся из found_at"
        )
    finally:
        conn.close()
