"""Interview Coach — досье к собеседованию.

Собирается из фактов: что в вакансии совпало с моими навыками, чего не
хватает, какие проекты показывать, что честно сказать про отсутствие
коммерческого опыта.

Формулировки вопросов работодателю и чек-лист — шаблонные, и это
допустимо: они не утверждают ничего обо мне. Всё, что утверждает, —
из мастер-профиля.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..connectors import Vacancy
from ..profile import MasterProfile, Project

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}")

QUESTIONS_TO_EMPLOYER = (
    "Как выглядит типичная задача в первые два месяца?",
    "Кто будет смотреть мой код и как устроено ревью?",
    "Какими инструментами с ИИ уже пользуется команда и что считается нормой?",
    "Что для вас будет признаком, что я справляюсь?",
    "Почему позиция открыта: рост команды или кто-то ушёл?",
)

CHECKLIST = (
    "перечитать вакансию и это досье",
    "открыть свои проекты и проверить, что ссылки работают",
    "подготовить рассказ о одном проекте на три минуты: задача, решение, результат",
    "проверить связь, камеру и микрофон заранее",
    "держать под рукой вопросы работодателю",
)


@dataclass
class Dossier:
    vacancy_title: str
    company: str | None
    url: str | None
    score: int | None
    matched: list[str] = field(default_factory=list)
    to_learn: list[str] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    honest_answer: str = ""
    questions: tuple[str, ...] = QUESTIONS_TO_EMPLOYER
    checklist: tuple[str, ...] = CHECKLIST


def _honest_answer(master: MasterProfile) -> str:
    """Про опыт — фактами, без оправданий и без преувеличений.

    Ответ зависит от того, что в профиле. Опыт в ИТ есть, но не на должности
    разработчика — об этом и говорим прямо: должность называлась иначе,
    а системы писал и внедрял.
    """
    projects = master.resume_projects
    working = [p for p in projects if p.in_production]
    measured = [p for p in projects if p.result]
    at_work = [p for p in projects if p.context and "рабоч" in p.context.casefold()]
    jobs = master.employment_history

    parts: list[str] = []
    if jobs:
        parts.append(
            f"На должности разработчика я не работал: моя должность — "
            f"{jobs[0].position.lower()}"
            + (f", {jobs[0].company}" if jobs[0].company else "")
            + "."
        )
        if at_work:
            parts.append(
                f"Но {len(at_work)} из систем, которые я показываю, написаны и "
                "внедрены именно там, на инфраструктуре работодателя: "
                + ", ".join(p.name or p.slug for p in at_work)
                + "."
            )
    else:
        parts.append("Коммерческого опыта по найму у меня нет, и я это не скрываю.")

    if working:
        parts.append(
            f"Работают у живых людей {len(working)}: "
            + ", ".join(p.name or p.slug for p in working)
            + "."
        )
    if measured:
        best = measured[0]
        parts.append(f"Результат измерен, не на словах: {best.name} — {best.result}")
    parts.append("Каждую вёл целиком: от требований до развёртывания и поддержки.")
    return " ".join(parts)


def prepare(
    vacancy: Vacancy,
    master: MasterProfile,
    *,
    score: int | None = None,
    limit_projects: int = 3,
) -> Dossier:
    text = " ".join(p for p in (vacancy.title, vacancy.description) if p)
    lowered = text.casefold()
    my_skills = master.known_skills
    mine = {s.casefold() for s in my_skills}

    terms = {t.casefold() for t in TOKEN.findall(text) if len(t) > 2}
    matched = sorted(t for t in terms if t in mine)
    matched += sorted(s.casefold() for s in my_skills if " " in s and s.casefold() in lowered)
    to_learn = sorted(terms - mine)

    projects = sorted(
        master.resume_projects,
        key=lambda p: -sum(1 for s in p.stack if s.casefold() in lowered),
    )[:limit_projects]

    return Dossier(
        vacancy_title=vacancy.title,
        company=vacancy.company,
        url=vacancy.url,
        score=score,
        matched=sorted(set(matched)),
        to_learn=to_learn,
        projects=projects,
        honest_answer=_honest_answer(master),
    )
