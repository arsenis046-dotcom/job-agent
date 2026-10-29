"""Критерий приёмки №2: Fact Checker с подброшенным ложным фактом
блокирует отправку."""

from __future__ import annotations

from jobagent.agents.fact_checker import FactChecker, merge
from jobagent.documents import Line, Section, build_resume


def test_настоящее_резюме_проходит(sample_profile):
    verdict = FactChecker(sample_profile).check_resume(build_resume(sample_profile))
    assert verdict.ok, str(verdict)
    assert verdict.checked > 0, "проверка должна что-то проверять"


def test_подброшенный_факт_в_резюме_блокирует(sample_profile):
    resume = build_resume(sample_profile)
    # подбрасываем опыт, которого в профиле нет
    resume.sections.append(
        Section(
            "Навыки",
            [
                Line(
                    kind="text",
                    evidence=("Kubernetes", "три года в найме"),
                    source="skills.tools",
                )
            ],
        )
    )
    verdict = FactChecker(sample_profile).check_resume(resume)
    assert not verdict.ok
    assert {p.claim for p in verdict.problems} == {"Kubernetes", "три года в найме"}
    assert "ЗАБЛОКИРОВАНА" in str(verdict)


def test_письмо_из_фактов_профиля_проходит(sample_profile):
    letter = (
        "Здравствуйте! NoteBot — телеграм-бот для заметок. "
        "Работаю с Python и SQLite. ivan@example.com"
    )
    verdict = FactChecker(sample_profile).check_letter(letter)
    assert verdict.ok, str(verdict)


def test_выдуманное_число_в_письме_блокирует(sample_profile):
    verdict = FactChecker(sample_profile).check_letter(
        "Здравствуйте! У меня 7 лет опыта работы с Python."
    )
    assert not verdict.ok
    assert any(p.claim == "7" and p.kind == "число" for p in verdict.problems)


def test_выдуманная_технология_в_письме_блокирует(sample_profile):
    verdict = FactChecker(sample_profile).check_letter(
        "Здравствуйте! Разворачивал проекты в Kubernetes."
    )
    assert not verdict.ok
    assert any(p.claim == "Kubernetes" for p in verdict.problems)


def test_выдуманная_ссылка_в_письме_блокирует(sample_profile):
    verdict = FactChecker(sample_profile).check_letter(
        "Мой проект: https://github.com/ivan/несуществующий-проект"
    )
    assert not verdict.ok
    assert any(p.kind == "ссылка" for p in verdict.problems)


def test_слова_из_вакансии_упоминать_можно(sample_profile):
    """ТЗ просит конкретику из вакансии — её запрещать нельзя."""
    vacancy = "Нужен разработчик в ООО Ромашка, стек FastAPI и Redis"
    verdict = FactChecker(sample_profile).check_letter(
        "Здравствуйте! Вакансия в ООО Ромашка интересна: FastAPI и Redis мне понятны.",
        vacancy_text=vacancy,
    )
    assert verdict.ok, str(verdict)


def test_чего_нет_ни_в_профиле_ни_в_вакансии_блокируется(sample_profile):
    verdict = FactChecker(sample_profile).check_letter(
        "Знаю Redis.", vacancy_text="Нужен разработчик, стек FastAPI"
    )
    assert not verdict.ok
    assert any(p.claim == "Redis" for p in verdict.problems)


def test_общий_вердикт_блокирует_если_блокирует_одна_проверка(sample_profile):
    checker = FactChecker(sample_profile)
    хороший = checker.check_resume(build_resume(sample_profile))
    плохой = checker.check_letter("Опыт 10 лет.")
    assert хороший.ok and not плохой.ok
    общий = merge(хороший, плохой)
    assert not общий.ok
    assert общий.checked == хороший.checked + плохой.checked
