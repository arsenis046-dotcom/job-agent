"""Что из мастер-профиля пригодится в новой сфере поиска.

Считаемая часть: какие мои навыки и проекты вообще попадают в ключевые слова
новой сферы. Если не попадает ничего — это главное, что нужно знать перед
сменой сферы, и сказать об этом должна программа, а не выяснить я на
двадцатом отказе.

Что писать в резюме для новой сферы — вопрос к модели и ко мне, а не
к считалке: тут агент только показывает, с чем идём.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import SearchProfile
from ..profile import MasterProfile


@dataclass
class Relevance:
    profile: str
    matching_skills: list[str] = field(default_factory=list)
    matching_projects: list[str] = field(default_factory=list)
    blocking_facts: list[str] = field(default_factory=list)

    @property
    def transferable(self) -> bool:
        return bool(self.matching_skills or self.matching_projects)

    def __str__(self) -> str:
        lines = [f"Профиль «{self.profile}»:"]
        if self.matching_skills:
            lines.append("  навыки в тему: " + ", ".join(self.matching_skills))
        if self.matching_projects:
            lines.append("  проекты в тему: " + ", ".join(self.matching_projects))
        if not self.transferable:
            lines.append(
                "  из мастер-профиля в эту сферу напрямую не переносится ничего: "
                "ни навыки, ни проекты не пересекаются с ключевыми словами. "
                "Резюме придётся строить на других фактах — и их надо внести "
                "в мастер-профиль, иначе писать будет нечего"
            )
        if self.blocking_facts:
            lines.append("  не хватает фактов: " + ", ".join(self.blocking_facts))
        return "\n".join(lines)


def relevance(search: SearchProfile, master: MasterProfile) -> Relevance:
    """Пересечение моих фактов с ключевыми словами сферы."""
    from ..profile import blocking_gaps

    haystack = " ".join(
        search.keywords
        + search.must_have_skills
        + search.nice_to_have_skills
        + [search.title or ""]
    ).casefold()

    skills = [s for s in master.known_skills if s.casefold() in haystack]
    projects = [
        project.name or project.slug
        for project in master.projects
        if any(item.casefold() in haystack for item in project.stack)
        or (project.one_liner or "").casefold() in haystack
    ]
    return Relevance(
        profile=search.name,
        matching_skills=skills,
        matching_projects=projects,
        blocking_facts=[gap.path for gap in blocking_gaps(master)],
    )
