"""Критерии приёмки раздела 10 ТЗ, которые не закрыты другими тестами.

Остальные живут там, где им место:
- dry_run ничего не отправляет, лимит и порог — `test_apply.py`;
- Fact Checker блокирует ложный факт — `test_fact_checker.py`;
- дубликаты откликов невозможны — `test_db.py` и `test_apply.py`;
- смена профиля не требует правки кода — `test_sphere.py`.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from jobagent.config import Mode
from jobagent.db import open_db, save_score, upsert_vacancy
from jobagent.pipeline import run_apply, run_score, run_search
from jobagent.profile import save_master_profile
from jobagent.reports.market import write_digest, write_market_report
from jobagent.reports.vacancies import write_report

# Ключ приложения уходит в заголовок, поэтому он латиницей, как настоящий.
# Пароль идёт в теле запроса, и там кириллица законна — пусть будет.
СЕКРЕТЫ = {
    "SUPERJOB_SECRET": "v3ry-s3cret-app-key-do-not-leak",
    "SUPERJOB_LOGIN": "secret-login@example.com",
    "SUPERJOB_PASSWORD": "очень-секретный-пароль",
    "SUPERJOB_CLIENT_ID": "9999999",
    "SUPERJOB_CV_ID": "25903399",
    "ANTHROPIC_API_KEY": "sk-ant-s3cret-key-do-not-leak",
}


@pytest.fixture
def с_секретами(repo, config, sample_profile, monkeypatch):
    for name, value in СЕКРЕТЫ.items():
        monkeypatch.setenv(name, value)
    save_master_profile(sample_profile, repo)
    return config


def вакансия(config, external_id: str = "1", verdict: str = "apply") -> int:
    conn = open_db(config.db_path)
    try:
        vacancy_id = upsert_vacancy(
            conn,
            platform="superjob",
            external_id=external_id,
            title="Разработчик с ИИ",
            description="Python, SQLite, автоматизация, LLM",
            url="https://superjob.ru/vakansii/1.html",
            salary_from=120000,
            currency="rub",
        )
        save_score(
            conn,
            vacancy_id,
            config.profile.name,
            score=90,
            verdict=verdict,
            reasoning="тестовая оценка",
            model="local",
        )
        return vacancy_id
    finally:
        conn.close()


@respx.mock
def test_секретов_нет_ни_в_базе_ни_в_документах(с_секретами):
    """Критерий 6: токены и ключи отсутствуют в БД и сгенерированных файлах."""
    вакансия(с_секретами)
    respx.post("https://api.superjob.ru/2.0/oauth2/password/").mock(
        return_value=httpx.Response(200, json={"access_token": "r.000.s3cret-token-do-not-leak"})
    )
    respx.post("https://api.superjob.ru/2.0/send_cv_on_vacancy/").mock(
        return_value=httpx.Response(200, json={"result": True})
    )
    auto = с_секретами.model_copy(
        update={"settings": с_секретами.settings.model_copy(update={"mode": Mode.auto})}
    )
    outcome = run_apply(auto)
    assert outcome.sent == 1, "отправка должна была случиться, иначе проверять нечего"

    пути = [с_секретами.db_path, *write_report(с_секретами), write_market_report(с_секретами), write_digest(с_секретами)]
    запретное = list(СЕКРЕТЫ.values()) + ["r.000.s3cret-token-do-not-leak"]
    for path in пути:
        данные = path.read_bytes()
        for секрет in запретное:
            assert секрет.encode("utf-8") not in данные, (
                f"{path.name}: внутри нашёлся секрет"
            )


@respx.mock
@pytest.mark.parametrize(
    "код,ожидаемое",
    [
        (401, "доступ не выдан"),
        (403, "доступ не выдан"),
        (429, "слишком часто"),
        (500, "недоступна"),
        (503, "недоступна"),
    ],
)
def test_ошибки_площадки_дают_понятное_сообщение(с_секретами, код, ожидаемое):
    """Критерий 7: при 401, 429 и 5xx работа продолжается с понятным сообщением."""
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=httpx.Response(код, json={"error": {"code": код}})
    )
    outcome = run_search(с_секретами)

    assert outcome.errors, "ошибка должна попасть в отчёт, а не в трассировку"
    assert ожидаемое in outcome.errors[0]
    for секрет in СЕКРЕТЫ.values():
        assert секрет not in outcome.errors[0], "в сообщении об ошибке нет секретов"


def test_испорченный_ключ_даёт_понятное_сообщение(с_секретами, monkeypatch):
    """Нелатинский символ в ключе — частая опечатка при копировании."""
    monkeypatch.setenv("SUPERJOB_SECRET", "ключ-с-кириллицей")
    outcome = run_search(с_секретами)
    assert outcome.errors
    assert "нелатинские символы" in outcome.errors[0]
    assert "ключ-с-кириллицей" not in outcome.errors[0], "ключ в сообщение не попадает"


@respx.mock
def test_ошибка_на_второй_странице_не_теряет_первую(с_секретами):
    """Критерий 7: без потери данных — найденное до сбоя остаётся в базе."""
    страницы = [
        httpx.Response(
            200,
            json={
                "objects": [
                    {
                        "id": 10,
                        "profession": "AI-разработчик",
                        "candidat": "Python, LLM",
                        "link": "https://superjob.ru/vakansii/10.html",
                    }
                ],
                "more": True,
            },
        ),
        httpx.Response(500, json={"error": {"code": 500}}),
    ]
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        side_effect=страницы + [страницы[1]] * 20
    )

    outcome = run_search(с_секретами)

    assert outcome.errors
    assert outcome.new == 1, "вакансия с первой страницы сохранена"
    conn = open_db(с_секретами.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) c FROM vacancies").fetchone()["c"] == 1
    finally:
        conn.close()


@respx.mock
def test_после_сбоя_оценка_работает_на_сохранённом(с_секретами):
    """Данные, собранные до ошибки, остаются пригодными для работы."""
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "objects": [
                        {
                            "id": 11,
                            "profession": "Разработчик с ИИ",
                            "candidat": "Python, LLM, автоматизация",
                            "link": "https://superjob.ru/vakansii/11.html",
                        }
                    ],
                    "more": False,
                },
            )
        ]
        + [httpx.Response(429, json={"error": {"code": 429}})] * 20
    )
    run_search(с_секретами)
    assert run_score(с_секретами).scored == 1
