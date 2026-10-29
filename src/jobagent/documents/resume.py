"""Сборка резюме из мастер-профиля.

Главная мысль: **текст строки нельзя написать, его можно только собрать
из фактов**. У каждой строки есть `evidence` — факты, взятые из профиля, и
необязательная подпись-ярлык из закрытого списка. Сам текст — вычисляемое
свойство. Поэтому выдуманный факт в резюме не может появиться технически,
а не только потому, что промпт просил этого не делать.

Базовое резюме собирается без модели вообще. Модель (Resume Writer, Фаза 1,
и адаптация под вакансию, Фаза 3) может только переупорядочить разделы и
выбрать, какие факты брать, — но не придумать новые.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ..profile import MasterProfile, Project, blocking_gaps

Kind = Literal["name", "subtitle", "contact", "text", "item", "detail"]

# Закрытый список ярлыков. Ничего, кроме них, в резюме не пишется.
LABELS = frozenset(
    {"Стек", "Моя роль", "Результат", "Ссылка", "Статус", "Уровень"}
)

# Стандартные заголовки, в фиксированном порядке — так их ждёт ATS.
HEADINGS = ("Контакты", "О себе", "Навыки", "Проекты", "Образование", "Языки", "Сертификаты")


class ResumeError(RuntimeError):
    """Резюме собрать нельзя: не хватает фактов."""


@dataclass(frozen=True)
class Line:
    kind: Kind
    evidence: tuple[str, ...]
    source: str
    label: str | None = None
    sep: str = ", "

    def __post_init__(self) -> None:
        if self.label is not None and self.label not in LABELS:
            raise ResumeError(f"ярлык {self.label!r} не из закрытого списка")
        if not self.evidence or not all(e.strip() for e in self.evidence):
            raise ResumeError(f"строка без фактов: {self.source}")

    @property
    def text(self) -> str:
        body = self.sep.join(self.evidence)
        return f"{self.label}: {body}" if self.label else body


@dataclass
class Section:
    heading: str
    lines: list[Line] = field(default_factory=list)

    def add(self, line: Line) -> None:
        self.lines.append(line)


@dataclass
class Resume:
    name: Line
    sections: list[Section] = field(default_factory=list)

    @property
    def all_lines(self) -> list[Line]:
        return [self.name] + [line for s in self.sections for line in s.lines]

    @property
    def headings(self) -> list[str]:
        return [s.heading for s in self.sections]

    def text(self) -> str:
        parts = [self.name.text]
        for section in self.sections:
            parts.append(section.heading)
            parts.extend(line.text for line in section.lines)
        return "\n".join(parts)


def _str(value: object) -> str:
    return str(value).strip()


def build_resume(profile: MasterProfile) -> Resume:
    """Базовое резюме. Падает, если не хватает обязательных фактов."""
    gaps = blocking_gaps(profile)
    if gaps:
        raise ResumeError(
            "не хватает фактов для резюме:\n"
            + "\n".join(f"  - {gap.path}: {gap.question}" for gap in gaps)
        )

    person, position = profile.person, profile.position
    resume = Resume(
        name=Line(
            kind="name",
            evidence=(_str(person.full_name),),
            source="person.full_name",
        )
    )

    # --- Контакты ----------------------------------------------------------
    contacts = Section("Контакты")
    title_evidence = [_str(position.target_titles[0])]
    title_source = "position.target_titles[0]"
    if position.level:
        title_evidence.append(_str(position.level))
    contacts.add(
        Line(
            kind="subtitle",
            evidence=tuple(title_evidence),
            source=title_source,
            sep=", ",
        )
    )
    for value, source in (
        (person.city, "person.city"),
        (person.email, "person.email"),
        (person.phone, "person.phone"),
        (person.telegram, "person.telegram"),
        (person.github, "person.github"),
        (person.website, "person.website"),
    ):
        if value:
            contacts.add(
                Line(kind="contact", evidence=(_str(value),), source=source)
            )
    resume.sections.append(contacts)

    # --- О себе ------------------------------------------------------------
    about = Section("О себе")
    about.add(Line(kind="text", evidence=(_str(profile.summary),), source="summary"))
    resume.sections.append(about)

    # --- Навыки ------------------------------------------------------------
    skills = Section("Навыки")
    for group, items in profile.skills.groups():
        if items:
            skills.add(
                Line(
                    kind="text",
                    evidence=tuple(_str(i) for i in items),
                    source=f"skills.{_group_field(group)}",
                )
            )
    resume.sections.append(skills)

    # --- Проекты -----------------------------------------------------------
    projects = Section("Проекты")
    for project in profile.resume_projects:
        projects.lines.extend(_project_lines(project))
    resume.sections.append(projects)

    # --- Образование, языки, сертификаты -----------------------------------
    if profile.education:
        education = Section("Образование")
        for i, item in enumerate(profile.education):
            evidence = tuple(
                _str(v)
                for v in (item.institution, item.speciality, item.degree, item.years)
                if v
            )
            if evidence:
                education.add(
                    Line(
                        kind="item",
                        evidence=evidence,
                        source=f"education[{i}]",
                        sep=", ",
                    )
                )
        resume.sections.append(education)

    if profile.languages:
        languages = Section("Языки")
        for i, lang in enumerate(profile.languages):
            evidence = (lang.name,) + ((_str(lang.level),) if lang.level else ())
            languages.add(
                Line(
                    kind="item",
                    evidence=evidence,
                    source=f"languages[{i}]",
                    sep=" — ",
                )
            )
        resume.sections.append(languages)

    if profile.certificates:
        certificates = Section("Сертификаты")
        for i, cert in enumerate(profile.certificates):
            evidence = (cert.name,) + tuple(
                _str(v) for v in (cert.issuer, cert.year, cert.url) if v
            )
            certificates.add(
                Line(
                    kind="item",
                    evidence=evidence,
                    source=f"certificates[{i}]",
                    sep=", ",
                )
            )
        resume.sections.append(certificates)

    return resume


def _group_field(group: str) -> str:
    return {
        "Языки программирования": "languages",
        "Инструменты": "tools",
        "Платформы": "platforms",
        "Базы данных": "databases",
        "Прочее": "other",
    }[group]


def _project_lines(project: Project) -> list[Line]:
    """Проект подаётся как проект: что это, стек, что сделал я, результат, ссылка."""
    base = f"projects[{project.slug}]"
    lines = [
        Line(
            kind="item",
            evidence=(_str(project.name), _str(project.one_liner)),
            source=f"{base}.name",
            sep=" — ",
        ),
        Line(
            kind="detail",
            evidence=tuple(_str(s) for s in project.stack),
            source=f"{base}.stack",
            label="Стек",
        ),
        Line(
            kind="detail",
            evidence=(_str(project.my_role),),
            source=f"{base}.my_role",
            label="Моя роль",
        ),
    ]
    if project.result:
        lines.append(
            Line(
                kind="detail",
                evidence=(_str(project.result),),
                source=f"{base}.result",
                label="Результат",
            )
        )
    if project.link:
        lines.append(
            Line(
                kind="detail",
                evidence=(_str(project.link),),
                source=f"{base}.repo_url",
                label="Ссылка",
            )
        )
    return lines
