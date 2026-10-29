"""Мастер-профиль — единственный источник фактов обо мне.

Схема нарочно строгая: `None` и пустой список означают «факт неизвестен»,
а не «нечего писать». Отсюда берётся отчёт о пробелах: что мешает собрать
резюме, а что просто желательно уточнить.

Коммерческого опыта у пользователя нет, и `employment_history` существует
только затем, чтобы его отсутствие было записано явно, а не додумывалось.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .config import ConfigError, project_root


class Person(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    city: str | None = None
    relocation: str | bool | None = None
    remote_only: bool | None = None
    email: str | None = None
    phone: str | None = None
    telegram: str | None = None
    github: str | None = None
    website: str | None = None


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_titles: list[str] = []
    level: str | None = None
    salary_expectation: str | int | None = None
    employment: list[str] = []
    schedule: list[str] = []


class Project(BaseModel):
    """Пет-проект. Подаётся как проект, а не как работа по найму."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str | None = None
    one_liner: str | None = None
    problem: str | None = None
    my_role: str | None = None
    stack: list[str] = []
    result: str | None = None
    in_production: bool | None = None
    users: str | int | None = None
    repo_url: str | None = None
    demo_url: str | None = None
    docs_url: str | None = None
    started: str | None = None
    status: str | None = None
    learned: list[str] = []

    @property
    def link(self) -> str | None:
        return self.repo_url or self.demo_url or self.docs_url

    @property
    def ready_for_resume(self) -> bool:
        """В резюме идут только проекты, о которых есть что сказать по делу."""
        return bool(self.name and self.one_liner and self.stack and self.my_role)


class Skills(BaseModel):
    model_config = ConfigDict(extra="forbid")

    languages: list[str] = []
    tools: list[str] = []
    platforms: list[str] = []
    databases: list[str] = []
    other: list[str] = []

    def groups(self) -> list[tuple[str, list[str]]]:
        return [
            ("Языки программирования", self.languages),
            ("Инструменты", self.tools),
            ("Платформы", self.platforms),
            ("Базы данных", self.databases),
            ("Прочее", self.other),
        ]

    @property
    def total(self) -> int:
        return sum(len(items) for _, items in self.groups())


class Education(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution: str | None = None
    speciality: str | None = None
    years: str | None = None
    degree: str | None = None


class Language(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    level: str | None = None


class Certificate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    issuer: str | None = None
    year: str | int | None = None
    url: str | None = None


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companies_to_avoid: list[str] = []
    industries_to_avoid: list[str] = []
    notes: str | None = None


class MasterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    person: Person = Person()
    position: Position = Position()
    summary: str | None = None
    employment_history: list[dict[str, Any]] = []
    projects: list[Project] = []
    skills: Skills = Skills()
    languages: list[Language] = []
    education: list[Education] = []
    certificates: list[Certificate] = []
    constraints: Constraints = Constraints()

    @property
    def resume_projects(self) -> list[Project]:
        return [p for p in self.projects if p.ready_for_resume]


@dataclass(frozen=True)
class Gap:
    """Незаполненный факт."""

    path: str
    question: str
    blocking: bool

    def __str__(self) -> str:
        mark = "обязательно" if self.blocking else "желательно"
        return f"[{mark}] {self.path}: {self.question}"


def profile_path(root: Path | None = None) -> Path:
    return (root or project_root()) / "profile" / "master_profile.yaml"


def projects_dir(root: Path | None = None) -> Path:
    return (root or project_root()) / "profile" / "projects"


def load_master_profile(root: Path | None = None) -> MasterProfile:
    """Читает мастер-профиль и подмешивает проекты из profile/projects/*.yaml."""
    path = profile_path(root)
    if not path.exists():
        raise ConfigError(f"нет мастер-профиля: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: ожидался словарь на верхнем уровне")

    known = {p.get("slug") for p in data.get("projects") or []}
    for file in sorted(projects_dir(root).glob("*.yaml")):
        if file.name.startswith("_"):  # _template.yaml и прочие заготовки
            continue
        project = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if project.get("slug") in known:
            continue
        data.setdefault("projects", []).append(project)

    return MasterProfile.model_validate(data)


HEADER = """# ЕДИНСТВЕННЫЙ источник фактов обо мне. Агентам запрещено добавлять сюда
# что-либо от себя и использовать в резюме то, чего здесь нет.
# Пустое значение (null) и пустой список означают «факт неизвестен»:
# агент обязан задать вопрос, а не домыслить. Описание полей — docs/profile.md
"""


def save_master_profile(
    profile: MasterProfile, root: Path | None = None
) -> Path:
    """Пишет профиль обратно. Комментарии не сохраняются — они в docs/profile.md."""
    path = profile_path(root)
    body = yaml.safe_dump(
        profile.model_dump(mode="json"),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    path.write_text(HEADER + "\n" + body, encoding="utf-8", newline="\n")
    return path


def find_gaps(profile: MasterProfile) -> list[Gap]:
    """Чего не хватает. Блокирующее — то, без чего резюме собрать нельзя."""
    gaps: list[Gap] = []
    p, pos = profile.person, profile.position

    def need(value: Any, path: str, question: str, blocking: bool) -> None:
        if value in (None, "", [], {}):
            gaps.append(Gap(path, question, blocking))

    need(p.full_name, "person.full_name", "Как тебя зовут полностью?", True)
    need(p.city, "person.city", "В каком городе ты находишься?", True)
    need(
        p.email or p.telegram or p.phone,
        "person.email",
        "Какой контакт указывать в резюме: почта, телефон, телеграм?",
        True,
    )
    need(
        pos.target_titles,
        "position.target_titles",
        "Как должна называться желаемая должность в резюме?",
        True,
    )
    need(pos.level, "position.level", "Какой уровень заявляем: junior или middle?", True)
    need(
        profile.summary,
        "summary",
        "Две-три фразы о себе — только факты, без лозунгов.",
        True,
    )
    need(
        profile.resume_projects,
        "projects",
        "Нужен хотя бы один проект с названием, описанием, стеком и твоей ролью.",
        True,
    )
    need(
        profile.skills.total or None,  # 0 навыков — это тоже пробел
        "skills",
        "Какими языками и инструментами ты реально владеешь?",
        True,
    )

    need(p.github, "person.github", "Ссылка на GitHub — её ждут в вакансиях.", False)
    need(
        pos.salary_expectation,
        "position.salary_expectation",
        "Какую зарплату указывать?",
        False,
    )
    need(pos.schedule, "position.schedule", "Формат работы: удалённо, офис, гибрид?", False)
    need(
        pos.employment,
        "position.employment",
        "Занятость: полная, частичная, проектная?",
        False,
    )
    need(
        p.relocation,
        "person.relocation",
        "Готов к переезду? Это отдельный фильтр в вакансиях.",
        False,
    )
    need(profile.languages, "languages", "Какими языками владеешь и на каком уровне?", False)
    need(profile.education, "education", "Образование: где, по какой специальности, годы.", False)

    for project in profile.projects:
        if project.ready_for_resume:
            if not project.link:
                gaps.append(
                    Gap(
                        f"projects[{project.slug}].repo_url",
                        f"Ссылка на «{project.name}» — без неё проект звучит голословно.",
                        False,
                    )
                )
            if not project.result:
                gaps.append(
                    Gap(
                        f"projects[{project.slug}].result",
                        f"Что получилось в «{project.name}»? Без выдуманных процентов.",
                        False,
                    )
                )
            continue
        missing = [
            field
            for field, value in (
                ("name", project.name),
                ("one_liner", project.one_liner),
                ("stack", project.stack),
                ("my_role", project.my_role),
            )
            if not value
        ]
        gaps.append(
            Gap(
                f"projects[{project.slug}]",
                f"Проект не готов для резюме, не хватает: {', '.join(missing)}.",
                False,
            )
        )

    return gaps


def blocking_gaps(profile: MasterProfile) -> list[Gap]:
    return [gap for gap in find_gaps(profile) if gap.blocking]
