"""Критерий приёмки: смена сферы поиска не требует правки кода.

Проверяется не словами, а делом: в тесте создаётся профиль сферы, не
связанной с IT, и по нему проходит весь конвейер — поиск, оценка, отчёт.
Ни одной ветки «если профиль такой-то» в коде для этого быть не должно.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from jobagent.agents.sphere import relevance
from jobagent.config import ConfigError, available_profiles, create_profile, load_config
from jobagent.db import open_db
from jobagent.pipeline import run_score, run_search
from jobagent.profile import save_master_profile
from jobagent.reports.market import write_market_report
from jobagent.reports.vacancies import write_report

ПОВАР = ["повар", "повар горячего цеха", "кухонный работник"]


def объявление(id_: int, profession: str, text: str, **kwargs) -> dict:
    return {
        "id": id_,
        "profession": profession,
        "candidat": text,
        "link": f"https://superjob.ru/vakansii/{id_}.html",
        "town": {"title": "Москва"},
        **kwargs,
    }


@pytest.fixture
def кухня(repo, sample_profile, monkeypatch):
    """Активный профиль — «повар», мастер-профиль остаётся айтишным."""
    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    save_master_profile(sample_profile, repo)
    create_profile(
        "povar", ПОВАР, title="Повар", threshold=60, root=repo
    )
    config = load_config(repo, profile="povar")
    limits = config.settings.limits.model_copy(update={"request_delay_seconds": 0.0})
    return config.model_copy(
        update={"settings": config.settings.model_copy(update={"limits": limits})}
    )


def test_созданный_профиль_сразу_валиден(repo):
    path = create_profile("barista", ["бариста", "кофейня"], root=repo)
    assert path.exists()
    assert "barista" in available_profiles(repo)
    профиль = load_config(repo, profile="barista").profile
    assert профиль.keywords == ["бариста", "кофейня"]
    assert профиль.score_threshold == 70


def test_имя_профиля_проверяется(repo):
    with pytest.raises(ConfigError):
        create_profile("Повар Кухни", ["повар"], root=repo)
    with pytest.raises(ConfigError):
        create_profile("cooking", [], root=repo)


def test_повторное_создание_не_затирает_файл(repo):
    create_profile("cooking", ["повар"], root=repo)
    with pytest.raises(ConfigError):
        create_profile("cooking", ["другое слово"], root=repo)


def test_агент_честно_говорит_что_переносить_нечего(кухня, sample_profile):
    """Главное, что нужно знать перед сменой сферы."""
    отчёт = relevance(кухня.profile, sample_profile)
    assert not отчёт.transferable
    assert "не переносится ничего" in str(отчёт)


def test_переносимое_показывается_если_оно_есть(repo, sample_profile):
    create_profile("python_dev", ["Python", "разработчик"], root=repo)
    отчёт = relevance(load_config(repo, profile="python_dev").profile, sample_profile)
    assert отчёт.transferable
    assert "Python" in отчёт.matching_skills
    assert "NoteBot" in отчёт.matching_projects


@respx.mock
def test_весь_конвейер_работает_на_профиле_вне_it(кухня):
    """Поиск, оценка и отчёты — тем же кодом, другим YAML."""
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=httpx.Response(
            200,
            json={
                "objects": [
                    объявление(
                        1,
                        "Повар горячего цеха",
                        "Ресторан ищет повара. Опыт работы на кухне, санминимум.",
                        payment_from=80000,
                    ),
                    объявление(
                        2,
                        "Кухонный работник",
                        "Мойка посуды, помощь повару, график 5/2",
                        payment_from=50000,
                    ),
                ],
                "more": False,
            },
        )
    )

    поиск = run_search(кухня)
    assert поиск.new == 2, "вакансии нашлись по русским ключевым словам"
    assert not поиск.errors

    оценка = run_score(кухня)
    assert оценка.scored == 2

    # мастер-профиль айтишный, поэтому высоких оценок тут быть не должно
    conn = open_db(кухня.db_path)
    try:
        вердикты = [
            r["verdict"]
            for r in conn.execute("SELECT verdict FROM scores WHERE profile = 'povar'")
        ]
    finally:
        conn.close()
    assert вердикты and "apply" not in вердикты, (
        "агент не должен уверенно советовать сферу, в которой у меня ничего нет"
    )

    md, csv = write_report(кухня)
    assert "Повар горячего цеха" in md.read_text(encoding="utf-8")
    assert "povar" in md.read_text(encoding="utf-8")
    assert csv.exists()

    рынок = write_market_report(кухня).read_text(encoding="utf-8")
    assert "Рынок по профилю «povar»" in рынок


@respx.mock
def test_оценки_разных_профилей_не_смешиваются(кухня, repo):
    """У каждой сферы своя оценка одной и той же вакансии."""
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=httpx.Response(
            200,
            json={
                "objects": [объявление(1, "Повар", "Кухня, санминимум")],
                "more": False,
            },
        )
    )
    run_search(кухня)
    run_score(кухня)

    другой = load_config(repo, profile="vibe_coding")
    assert run_score(другой).scored == 1, "для другого профиля вакансия ещё не оценена"

    conn = open_db(кухня.db_path)
    try:
        профили = {
            r["profile"] for r in conn.execute("SELECT profile FROM scores")
        }
    finally:
        conn.close()
    assert профили == {"povar", "vibe_coding"}
