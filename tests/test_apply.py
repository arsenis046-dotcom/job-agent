"""Критерии приёмки по откликам: dry_run ничего не отправляет, дубликаты
невозможны, дневной лимит и порог соблюдаются."""

from __future__ import annotations

import httpx
import pytest
import respx

from jobagent.config import Mode
from jobagent.db import now, open_db, save_score, upsert_vacancy
from jobagent.pipeline import run_apply
from jobagent.profile import save_master_profile


@pytest.fixture
def готово(repo, config, sample_profile, monkeypatch):
    """Профиль заполнен, ключи заданы, в базе есть вакансии с вердиктом apply."""
    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    monkeypatch.setenv("SUPERJOB_LOGIN", "me@example.com")
    monkeypatch.setenv("SUPERJOB_PASSWORD", "pwd")
    monkeypatch.setenv("SUPERJOB_CLIENT_ID", "2345")
    monkeypatch.setenv("SUPERJOB_CV_ID", "25903399")
    save_master_profile(sample_profile, repo)
    return config


def добавить(config, count: int, verdict: str = "apply", score: int = 90) -> list[int]:
    conn = open_db(config.db_path)
    try:
        ids = []
        for i in range(count):
            vacancy_id = upsert_vacancy(
                conn,
                platform="superjob",
                external_id=str(100 + i),
                title=f"Разработчик с ИИ {i}",
                description="Python, SQLite, автоматизация, LLM",
                url=f"https://superjob.ru/vakansii/{100 + i}.html",
            )
            save_score(
                conn,
                vacancy_id,
                config.profile.name,
                score=score,
                verdict=verdict,
                reasoning="тестовая оценка",
                model="local",
            )
            ids.append(vacancy_id)
        return ids
    finally:
        conn.close()


def заявки(config) -> list[tuple[str, str | None]]:
    conn = open_db(config.db_path)
    try:
        return [
            (r["status"], r["sent_at"])
            for r in conn.execute("SELECT status, sent_at FROM applications")
        ]
    finally:
        conn.close()


@respx.mock
def test_в_dry_run_ни_один_отклик_не_уходит(готово):
    добавить(готово, 2)
    route = respx.route().mock(side_effect=AssertionError("запрос в dry_run"))

    outcome = run_apply(готово)

    assert готово.settings.mode is Mode.dry_run
    assert (outcome.sent, outcome.drafts) == (0, 2)
    assert route.call_count == 0
    assert all(status == "draft" and sent is None for status, sent in заявки(готово))


@respx.mock
def test_тексты_резюме_и_письма_сохраняются(готово):
    добавить(готово, 1)
    respx.route().mock(side_effect=AssertionError("запрос в dry_run"))
    run_apply(готово)

    conn = open_db(готово.db_path)
    try:
        row = conn.execute(
            "SELECT resume_text, letter_text, mode FROM applications"
        ).fetchone()
    finally:
        conn.close()
    assert "Иван Петров" in row["resume_text"], "резюме сохранено целиком"
    assert "NoteBot" in row["letter_text"], "письмо сохранено целиком"
    assert row["mode"] == "dry_run"


@respx.mock
def test_дневной_лимит_соблюдается(готово):
    добавить(готово, 5)
    respx.route().mock(side_effect=AssertionError("запрос в dry_run"))
    малый = готово.model_copy(
        update={
            "settings": готово.settings.model_copy(
                update={
                    "limits": готово.settings.limits.model_copy(
                        update={"applications_per_day": 2}
                    )
                }
            )
        }
    )

    outcome = run_apply(малый)

    assert outcome.budget == 2
    assert outcome.drafts == 2, "больше лимита за запуск не берём"
    assert any("лимит исчерпан" in note for note in outcome.notes)


@respx.mock
def test_вердикт_ниже_порога_не_отправляется(готово):
    добавить(готово, 3, verdict="skip", score=40)
    добавить(готово, 0)
    route = respx.route().mock(side_effect=AssertionError("отправка отсеянного"))

    outcome = run_apply(готово)

    assert (outcome.sent, outcome.drafts) == (0, 0)
    assert route.call_count == 0
    assert заявки(готово) == []


@respx.mock
def test_в_auto_отклик_уходит_и_дубликат_невозможен(готово):
    добавить(готово, 1)
    respx.post("https://api.superjob.ru/2.0/oauth2/password/").mock(
        return_value=httpx.Response(200, json={"access_token": "r.000.token"})
    )
    send = respx.post("https://api.superjob.ru/2.0/send_cv_on_vacancy/").mock(
        return_value=httpx.Response(200, json={"result": True})
    )
    auto = готово.model_copy(
        update={"settings": готово.settings.model_copy(update={"mode": Mode.auto})}
    )

    first = run_apply(auto)
    assert (first.sent, send.call_count) == (1, 1)
    статусы = заявки(auto)
    assert [s for s, _ in статусы] == ["sent"]
    assert статусы[0][1] is not None, "у отправленного отклика есть время отправки"

    # второй запуск: вакансия уже с отправленным откликом
    second = run_apply(auto)
    assert second.sent == 0, "повторный отклик на ту же вакансию не отправляется"
    assert send.call_count == 1, "и запроса второй раз не было"


@respx.mock
def test_заблокированное_проверкой_письмо_не_отправляется(готово, sample_profile, repo):
    """Если в профиле нет фактов для письма, отправка останавливается."""
    добавить(готово, 1)
    # профиль без навыков и проектов: письму нечем подтверждаться,
    # а резюме и вовсе не собирается — отправки быть не должно
    from jobagent.profile import MasterProfile

    save_master_profile(MasterProfile(), repo)
    route = respx.route().mock(side_effect=AssertionError("отправка без фактов"))

    from jobagent.documents import ResumeError

    with pytest.raises(ResumeError):
        run_apply(готово)
    assert route.call_count == 0
    assert заявки(готово) == []
