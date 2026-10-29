"""Letter Writer — сопроводительное письмо.

Как и резюме, письмо пока собирается из фактов, без модели: каждое
предложение склеивается из значений мастер-профиля и слов самой вакансии.
Получается суховато, зато Fact Checker такое письмо пропускает по
определению, а не по счастливой случайности.

Движок на модели встанет рядом, через тот же интерфейс `write()`. Проверка
останется та же: письмо от модели пройдёт через Fact Checker наравне
с этим, и выдуманный факт заблокирует отправку.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel
from pydantic import Field as PydanticField

from ..config import Config
from ..connectors import Vacancy
from ..profile import MasterProfile, Project
from .llm import LLM

# ТЗ: письмо до ~150 слов. Длинные письма не читают.
MAX_WORDS = 150


@dataclass
class Letter:
    text: str
    engine: str
    projects: list[str]

    @property
    def words(self) -> int:
        return len(self.text.split())


class TemplateLetterWriter:
    """Письмо из фактов. Ни одного утверждения, которого нет в профиле."""

    engine = "template"

    def __init__(self, profile: MasterProfile) -> None:
        self.profile = profile

    def relevant_projects(self, vacancy: Vacancy, limit: int = 2) -> list[Project]:
        """Проекты, чей стек упоминается в вакансии; иначе — первые по порядку."""
        text = " ".join(p for p in (vacancy.title, vacancy.description) if p).casefold()
        ranked = sorted(
            self.profile.resume_projects,
            key=lambda p: -sum(1 for s in p.stack if s.casefold() in text),
        )
        return ranked[:limit]

    def write(self, vacancy: Vacancy) -> Letter:
        person = self.profile.person
        projects = self.relevant_projects(vacancy)

        parts: list[str] = [f"Здравствуйте! Пишу по вакансии «{vacancy.title}»."]

        for project in projects:
            sentence = f"{project.name} — {project.one_liner}"
            if project.my_role:
                sentence += f". {project.my_role[0].upper()}{project.my_role[1:]}"
            if project.result:
                sentence += f". {project.result[0].upper()}{project.result[1:]}"
            if project.link:
                sentence += f". {project.link}"
            parts.append(sentence.rstrip(".") + ".")

        стек = ", ".join(self.profile.skills.languages + self.profile.skills.tools[:3])
        if стек:
            parts.append(f"Работаю с этим: {стек}.")

        contact = person.email or person.telegram or person.phone
        if contact:
            parts.append(f"Буду рад обсудить. {contact}")

        text = _fit(" ".join(parts), MAX_WORDS)
        return Letter(text=text, engine=self.engine, projects=[p.slug for p in projects])


class ModelLetter(BaseModel):
    """Схема ответа модели."""

    text: str
    used_projects: list[str] = PydanticField(default_factory=list)


SYSTEM = f"""Ты пишешь сопроводительное письмо к отклику на вакансию.

Жёсткие правила:
- Только факты, которые тебе даны. Ни одного утверждения о соискателе,
  которого нет в списке фактов: ни лет опыта, ни технологий, ни цифр.
  Любая выдумка будет поймана проверкой и отправка сорвётся.
- Не больше {MAX_WORDS} слов. Короче — лучше.
- Никаких «коммуникабельный», «быстро обучаюсь», «амбициозный»: это не факты.
- Одно-два конкретных совпадения с вакансией, остальное убрать.
- Обращение на «вы», без заискивания и без фамильярности.
- Упоминать технологии и компанию из текста вакансии можно.
- В конце — контакт из фактов, как он дан."""


class ModelLetterWriter:
    """Письмо моделью. Проходит тот же Fact Checker, что и шаблонное."""

    def __init__(self, config: Config, profile: MasterProfile) -> None:
        self.config = config
        self.profile = profile
        self.llm = LLM(config)
        self._template = TemplateLetterWriter(profile)

    @property
    def engine(self) -> str:
        return self.llm.model

    def write(self, vacancy: Vacancy) -> Letter:
        projects = self._template.relevant_projects(vacancy)
        person = self.profile.person
        facts = [
            "# Факты о соискателе",
            f"Имя: {person.full_name}",
            f"Город: {person.city}",
            f"Контакт: {person.email or person.telegram or person.phone}",
            f"Должность, на которую идёт: {self.profile.position.target_titles[0]}",
            f"Уровень: {self.profile.position.level or 'не указан'}",
            f"О себе: {self.profile.summary}",
            "Навыки: " + ", ".join(self.profile.known_skills),
            "",
            "# Подходящие системы, о которых можно рассказать",
        ]
        for project in projects:
            facts.append(
                f"- {project.name}: {project.one_liner}. "
                f"Где сделано: {project.context or 'не указано'}. "
                f"Моя роль: {project.my_role}. "
                f"Результат: {project.result or 'не измерен'}. "
                f"Ссылка: {project.link or 'нет'}"
            )
        facts += [
            "",
            "# Вакансия",
            f"Название: {vacancy.title}",
            f"Компания: {vacancy.company or 'не указана'}",
            f"Описание:\n{vacancy.description or '(описания нет)'}",
        ]

        answer = self.llm.parse(
            ModelLetter, system=SYSTEM, prompt="\n".join(facts), effort="low"
        )
        return Letter(
            text=answer.text.strip(),
            engine=self.engine,
            projects=answer.used_projects or [p.slug for p in projects],
        )


def build_letter_writer(config: Config, profile: MasterProfile, platform: str):
    """Движок под площадку: модель только там, где это разрешено и возможно."""
    settings = config.settings.platform(platform)
    if settings.use_model and LLM(config).available:
        return ModelLetterWriter(config, profile)
    return TemplateLetterWriter(profile)


def _fit(text: str, max_words: int) -> str:
    """Укладывает письмо в лимит, отбрасывая предложения с конца.

    Обрезать по словам нельзя: получится оборванная фраза, и Fact Checker
    справедливо увидит в ней искажённый факт.
    """
    if len(text.split()) <= max_words:
        return text
    sentences = [s.strip() for s in text.split(". ") if s.strip()]
    while len(sentences) > 1 and len(". ".join(sentences).split()) > max_words:
        sentences.pop(-2)  # последнее предложение — контакт, его оставляем
    return ". ".join(sentences)
