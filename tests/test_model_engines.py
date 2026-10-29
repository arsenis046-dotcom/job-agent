"""Движки на модели: две главные гарантии и поведение при осечках.

Живых вызовов здесь нет и быть не должно: вместо `LLM` подставляется
заглушка, которая записывает, о чём её спросили. Это и позволяет
проверить то, что иначе держалось бы на внимательности — что текст
вакансий hh.ru наружу не уходит.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from jobagent.agents import letter as letter_module
from jobagent.agents import matcher as matcher_module
from jobagent.agents.letter import (
    Letter,
    ModelLetterWriter,
    TemplateLetterWriter,
    build_letter_writer,
)
from jobagent.agents.llm import LLMError, LLMRefused, LLMUnavailable
from jobagent.agents.matcher import (
    APPLY,
    ASK,
    SKIP,
    LocalMatcher,
    ModelAnswer,
    ModelMatcher,
    build_matcher,
    verdict_for,
)
from jobagent.config import Mode
from jobagent.connectors import Vacancy
from jobagent.db import open_db, save_score, upsert_vacancy
from jobagent.pipeline import run_apply, run_score
from jobagent.profile import save_master_profile


class StubLLM:
    """Заглушка модели: отдаёт заготовленный ответ и помнит все вопросы."""

    model = "stub-model"
    available = True

    def __init__(self, answer=None, raises: Exception | None = None) -> None:
        self.answer = answer
        self.raises = raises
        self.calls: list[dict] = []

    def parse(self, schema, *, system: str, prompt: str, effort: str = "low"):
        self.calls.append(
            {"schema": schema, "system": system, "prompt": prompt, "effort": effort}
        )
        if self.raises is not None:
            raise self.raises
        return self.answer


def вакансия(platform: str = "superjob", **kwargs) -> Vacancy:
    defaults = {
        "platform": platform,
        "external_id": "1",
        "title": "AI-разработчик",
        "description": "Нужен Python, Docker, LLM. Удалённо.",
        "company": "ООО Пример",
    }
    return Vacancy(**{**defaults, **kwargs})


ОТВЕТ = ModelAnswer(
    score=84,
    matched=["Python", "Docker"],
    gaps=["Kubernetes"],
    reasoning="Совпадает стек и формат работы.",
)


def модельный_матчер(config, profile, llm: StubLLM) -> ModelMatcher:
    m = ModelMatcher(config, profile)
    m.llm = llm
    return m


# --- гарантия 1: hh.ru наружу не уходит -----------------------------------


def test_для_hh_выбирается_локальный_движок(config, sample_profile, monkeypatch):
    """Даже когда ключ есть: п. 4.6 Условий hh.ru запрещает передачу данных."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ключ-есть")
    assert config.settings.platform("hh").use_model is False

    assert isinstance(build_matcher(config, sample_profile, "hh"), LocalMatcher)
    assert isinstance(build_letter_writer(config, sample_profile, "hh"), TemplateLetterWriter)


def test_для_superjob_выбирается_модель(config, sample_profile, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ключ-есть")
    assert config.settings.platform("superjob").use_model is True

    assert isinstance(build_matcher(config, sample_profile, "superjob"), ModelMatcher)
    assert isinstance(
        build_letter_writer(config, sample_profile, "superjob"), ModelLetterWriter
    )


def test_без_ключа_модель_не_выбирается(config, sample_profile, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("jobagent.agents.matcher.LLM.available", False)
    assert isinstance(build_matcher(config, sample_profile, "superjob"), LocalMatcher)


def test_текст_вакансии_hh_не_уходит_в_модель(repo, config, sample_profile, monkeypatch):
    """Сквозная проверка: при оценке вакансий hh.ru обращений к модели нет."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ключ-есть")
    save_master_profile(sample_profile, repo)

    def запрещено(*args, **kwargs):
        raise AssertionError("текст вакансии hh.ru ушёл в модель")

    monkeypatch.setattr("jobagent.agents.llm.LLM.parse", запрещено)

    conn = open_db(config.db_path)
    try:
        upsert_vacancy(
            conn,
            platform="hh",
            external_id="42",
            title="Разработчик с ИИ",
            description="Python, LLM, автоматизация — секретный текст вакансии",
        )
    finally:
        conn.close()

    outcome = run_score(config)

    assert outcome.scored == 1, "вакансия всё равно должна быть оценена"
    conn = open_db(config.db_path)
    try:
        row = conn.execute("SELECT model, score FROM scores").fetchone()
    finally:
        conn.close()
    assert row["model"] == "local"
    assert row["score"] is not None


# --- гарантия 2: выдумка модели блокирует отправку ------------------------


@respx.mock
def test_выдуманный_моделью_факт_блокирует_отправку(
    repo, config, sample_profile, monkeypatch
):
    """Модель написала «7 лет опыта» — Fact Checker обязан остановить отправку."""
    for name, value in {
        "ANTHROPIC_API_KEY": "sk-ant-ключ-есть",
        "SUPERJOB_SECRET": "app-key",
        "SUPERJOB_LOGIN": "me@example.com",
        "SUPERJOB_PASSWORD": "pwd",
        "SUPERJOB_CLIENT_ID": "2345",
        "SUPERJOB_CV_ID": "25903399",
    }.items():
        monkeypatch.setenv(name, value)
    save_master_profile(sample_profile, repo)

    monkeypatch.setattr(
        ModelLetterWriter,
        "write",
        lambda self, vacancy: Letter(
            text="Здравствуйте! У меня 7 лет опыта и я работал с Kubernetes.",
            engine="stub-model",
            projects=[],
        ),
    )
    отправка = respx.route().mock(
        side_effect=AssertionError("ушла отправка с выдуманным фактом")
    )

    conn = open_db(config.db_path)
    try:
        vacancy_id = upsert_vacancy(
            conn,
            platform="superjob",
            external_id="7",
            title="AI-разработчик",
            description="Python, Docker",
        )
        save_score(
            conn, vacancy_id, config.profile.name, score=90, verdict=APPLY, model="stub"
        )
    finally:
        conn.close()

    auto = config.model_copy(
        update={"settings": config.settings.model_copy(update={"mode": Mode.auto})}
    )
    outcome = run_apply(auto)

    assert outcome.blocked == 1
    assert outcome.sent == 0
    assert отправка.call_count == 0
    conn = open_db(config.db_path)
    try:
        row = conn.execute("SELECT status, letter_text FROM applications").fetchone()
    finally:
        conn.close()
    assert row["status"] == "blocked"
    assert "7 лет" in row["letter_text"], "текст сохранён, чтобы было видно причину"


# --- вердикт считает программа --------------------------------------------


def test_вердикт_считается_по_порогу_а_не_моделью(config, sample_profile):
    """Модель даёт оценку, решение принимает программа."""
    llm = StubLLM(ОТВЕТ)
    низкий = модельный_матчер(config, sample_profile, llm).assess(вакансия())
    assert низкий.verdict == APPLY, "84 при пороге 70 — откликаться"

    строгий = config.model_copy(
        update={"profile": config.profile.model_copy(update={"score_threshold": 95})}
    )
    высокий = модельный_матчер(строгий, sample_profile, StubLLM(ОТВЕТ)).assess(вакансия())
    assert высокий.score == 84, "оценка модели не меняется"
    assert высокий.verdict == ASK, "а решение — меняется: 84 до порога 95 не дотянуло"

    очень_строгий = config.model_copy(
        update={"profile": config.profile.model_copy(update={"score_threshold": 100})}
    )
    assert (
        модельный_матчер(очень_строгий, sample_profile, StubLLM(ОТВЕТ))
        .assess(вакансия())
        .verdict
        == SKIP
    )


def test_оценка_и_обоснование_берутся_у_модели(config, sample_profile):
    result = модельный_матчер(config, sample_profile, StubLLM(ОТВЕТ)).assess(вакансия())
    assert result.score == 84
    assert result.matched == ["Python", "Docker"]
    assert result.gaps == ["Kubernetes"]
    assert result.reasoning == "Совпадает стек и формат работы."
    assert result.engine == "stub-model"


def test_стоп_фактор_проверяется_механически(config, sample_profile):
    """Доверять модели механическую проверку незачем."""
    llm = StubLLM(ОТВЕТ)
    result = модельный_матчер(config, sample_profile, llm).assess(
        вакансия(description="Python, Docker. Релокация обязательна.")
    )
    assert result.verdict == SKIP
    assert result.deal_breakers == ["релокация обязательна"]
    assert result.score == 0, "высокая оценка модели не спасает от стоп-фактора"


def test_вакансия_без_описания_в_модель_не_отправляется(config, sample_profile):
    llm = StubLLM(ОТВЕТ)
    result = модельный_матчер(config, sample_profile, llm).assess(
        вакансия(description=None)
    )
    assert result.verdict == ASK
    assert llm.calls == [], "платить за пустой запрос незачем"


def test_в_промпт_попадают_только_мои_навыки(config, sample_profile):
    llm = StubLLM(ОТВЕТ)
    модельный_матчер(config, sample_profile, llm).assess(вакансия())
    prompt = llm.calls[0]["prompt"]
    assert "закрытый список" in llm.calls[0]["system"]
    assert "Python" in prompt
    assert "Kubernetes" not in prompt.split("# Вакансия")[0], (
        "чего у меня нет, в разделе фактов быть не должно"
    )


def test_порог_не_зависит_от_источника_оценки(config):
    assert verdict_for(90, 70) == APPLY
    assert verdict_for(60, 70) == ASK
    assert verdict_for(40, 70) == SKIP
    assert verdict_for(None, 70) == ASK, "нет оценки — спроси меня"


# --- осечки модели не роняют работу ---------------------------------------


@pytest.mark.parametrize(
    "сбой",
    [
        LLMUnavailable("ключа нет"),
        LLMRefused("классификатор отказал"),
        LLMError("Anthropic API: лимит запросов (429), нужна пауза"),
    ],
)
def test_при_осечке_модели_оценка_делается_локально(
    repo, config, sample_profile, monkeypatch, сбой
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ключ-есть")
    save_master_profile(sample_profile, repo)
    monkeypatch.setattr(
        "jobagent.agents.llm.LLM.parse",
        lambda *args, **kwargs: (_ for _ in ()).throw(сбой),
    )

    conn = open_db(config.db_path)
    try:
        upsert_vacancy(
            conn,
            platform="superjob",
            external_id="1",
            title="Разработчик с ИИ",
            description="Python, Docker, LLM, автоматизация, удалённо",
        )
    finally:
        conn.close()

    outcome = run_score(config)

    assert outcome.scored == 1
    assert outcome.fallbacks, "откат должен быть замечен, а не проглочен"
    assert str(сбой) in outcome.fallbacks[0]
    conn = open_db(config.db_path)
    try:
        assert conn.execute("SELECT model FROM scores").fetchone()["model"] == "local"
    finally:
        conn.close()


def test_ошибка_api_объясняет_причину(config):
    """400 с текстом «кончились средства» не должен превращаться в «ошибка 400»."""
    from jobagent.agents.llm import LLM

    class Ошибка(Exception):
        status_code = 400
        message = "Your credit balance is too low to access the Anthropic API."
        body = {"error": {"message": message}}

    текст = LLM(config)._explain(Ошибка())
    assert "400" in текст
    assert "credit balance is too low" in текст


def test_коды_401_и_429_объясняются_своими_словами(config):
    from jobagent.agents.llm import LLM

    llm = LLM(config)
    assert "ключ не принят" in llm._explain(type("E", (), {"status_code": 401})())
    assert "лимит запросов" in llm._explain(type("E", (), {"status_code": 429})())
    assert "недоступен" in llm._explain(type("E", (), {"status_code": 503})())


def test_письмо_от_модели_проходит_ту_же_проверку(config, sample_profile):
    """Схема ответа и сборка письма — на месте, без обращения наружу."""
    writer = ModelLetterWriter(config, sample_profile)
    writer.llm = StubLLM(
        letter_module.ModelLetter(
            text="Здравствуйте! NoteBot — телеграм-бот для заметок. ivan@example.com",
            used_projects=["notebot"],
        )
    )
    письмо = writer.write(вакансия())

    assert письмо.engine == "stub-model"
    assert письмо.projects == ["notebot"]

    from jobagent.agents.fact_checker import FactChecker

    assert FactChecker(sample_profile).check_letter(письмо.text).ok


def test_в_промпт_письма_попадает_где_сделан_проект(config, sample_profile):
    """Контекст проекта нужен модели: иначе она назовёт рабочую задачу своей."""
    project = sample_profile.projects[0].model_copy(
        update={"context": "рабочая задача"}
    )
    profile = sample_profile.model_copy(update={"projects": [project]})
    writer = ModelLetterWriter(config, profile)
    writer.llm = StubLLM(letter_module.ModelLetter(text="Здравствуйте!"))

    writer.write(вакансия())

    assert "Где сделано: рабочая задача" in writer.llm.calls[0]["prompt"]
