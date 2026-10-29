from __future__ import annotations

import pytest

from jobagent.agents.matcher import APPLY, ASK, SKIP, LocalMatcher
from jobagent.connectors import Vacancy


def vacancy(title: str, description: str | None = None, **kwargs) -> Vacancy:
    return Vacancy(
        platform="superjob",
        external_id="1",
        title=title,
        description=description,
        **kwargs,
    )


@pytest.fixture
def matcher(config, sample_profile):
    """Профиль поиска как в репозитории: заполнен не полностью."""
    return LocalMatcher(config, sample_profile)


@pytest.fixture
def filled(config, sample_profile):
    """Профиль поиска с заданными зарплатой, форматом и обязательными навыками."""
    search = config.profile.model_copy(
        update={
            "must_have_skills": ["Python"],
            "nice_to_have_skills": ["Docker"],
            "salary_min": 100000,
            "schedule": ["удалённо"],
        }
    )
    return LocalMatcher(config.model_copy(update={"profile": search}), sample_profile)


def test_без_описания_оценки_нет_а_есть_вопрос(matcher):
    result = matcher.assess(vacancy("AI-разработчик"))
    assert result.score is None
    assert result.verdict == ASK
    assert "описания" in result.reasoning


def test_стоп_фактор_отсекает_независимо_от_совпадений(matcher):
    result = matcher.assess(
        vacancy(
            "AI-разработчик LLM",
            "Ищем промпт-инженера, Python, Claude Code. Релокация обязательна.",
        )
    )
    assert result.verdict == SKIP
    assert result.score == 0
    assert result.deal_breakers == ["релокация обязательна"]


def test_исключающее_слово_в_названии_отсекает(matcher):
    result = matcher.assess(
        vacancy("Senior разработчик с ИИ", "Python, LLM, Claude Code, автоматизация")
    )
    assert result.verdict == SKIP
    assert "senior" in [g.casefold() for g in result.gaps]


def test_подходящая_вакансия_получает_высокую_оценку(filled):
    result = filled.assess(
        vacancy(
            "Разработчик с ИИ",
            "Нужен промпт-инженер: Python, Docker, git, LLM, автоматизация, удалённо",
            salary_from=150000,
        )
    )
    assert result.verdict == APPLY
    assert result.score is not None and result.score >= filled.config.threshold
    assert "Python" in result.matched


def test_при_незаполненном_профиле_поиска_оценка_не_доверяется(matcher):
    """Два совпавших слова не должны давать «откликаться» на пустом профиле."""
    result = matcher.assess(
        vacancy("Разработчик с ИИ", "Python, Docker, git, LLM, автоматизация")
    )
    assert result.verdict == ASK, "без различающих признаков верить оценке нельзя"
    assert result.score == 75, "оценка ограничена потолком"
    assert "ненадёжна" in result.reasoning


def test_низкая_зарплата_это_пробел(filled):
    result = filled.assess(
        vacancy(
            "Разработчик с ИИ",
            "Python, LLM, автоматизация, удалённо",
            salary_from=40000,
            salary_to=60000,
        )
    )
    assert any("зарплата ниже" in gap for gap in result.gaps)


def test_слабое_совпадение_уходит_в_пропуск(matcher):
    result = matcher.assess(
        vacancy("Менеджер по продажам", "Холодные звонки, работа с клиентами, CRM")
    )
    assert result.verdict == SKIP
    assert result.score is not None and result.score < matcher.config.threshold


def test_совпадение_ищется_по_словам_а_не_подстрокам(matcher):
    """«LLM» не должно находиться внутри «HTML»."""
    result = matcher.assess(vacancy("Верстальщик", "HTML, CSS, вёрстка писем"))
    assert "LLM" not in result.matched


def test_в_обоснование_всегда_написано_чем_считали(matcher):
    result = matcher.assess(vacancy("Разработчик", "Python, LLM"))
    assert "локальная оценка" in result.reasoning
    assert result.engine == "local"


def test_порог_профиля_влияет_на_вердикт(filled, sample_profile):
    """Та же вакансия при высоком пороге получает «спросить», а не «откликаться»."""
    # совпадают слова и навыки, но зарплата ниже и формат не тот — середина
    спорная = vacancy(
        "Разработчик с ИИ",
        "Нужен промпт-инженер: Python, git, Claude Code, LLM, автоматизация. Офис.",
        salary_from=40000,
    )
    мягкий = filled.assess(спорная)

    строгий_config = filled.config.model_copy(
        update={"profile": filled.search.model_copy(update={"score_threshold": 95})}
    )
    строгий = LocalMatcher(строгий_config, sample_profile).assess(спорная)

    assert мягкий.score == строгий.score, "оценка не зависит от порога"
    assert мягкий.verdict == ASK, "при пороге 70 середина достойна вопроса"
    assert строгий.verdict == SKIP, "при пороге 95 та же вакансия отсеивается"
