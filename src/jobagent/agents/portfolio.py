"""Portfolio Advisor — чем портфолио не закрывает рынок.

Считаемая часть советов здесь честная и проверяемая: какие требования
встречаются в вакансиях часто, какие из них закрыты моими навыками, а какие
нет, и какие мои проекты отвечают на самые частые запросы. Придумывать новые
проекты — дело модели, и это будет отдельный движок; пока агент показывает
пробелы и не выдаёт за совет собственные фантазии.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..profile import MasterProfile, Project
from .market import MarketReport


@dataclass
class ProjectCoverage:
    project: Project
    terms: list[str] = field(default_factory=list)

    @property
    def weight(self) -> int:
        return len(self.terms)


@dataclass
class Advice:
    covered: list[tuple[str, int]] = field(default_factory=list)
    gaps: list[tuple[str, int]] = field(default_factory=list)
    strongest: list[ProjectCoverage] = field(default_factory=list)
    unused_projects: list[str] = field(default_factory=list)
    vacancies: int = 0

    @property
    def coverage_percent(self) -> int | None:
        total = len(self.covered) + len(self.gaps)
        return round(100 * len(self.covered) / total) if total else None


def advise(report: MarketReport, master: MasterProfile, top_gaps: int = 10) -> Advice:
    """Что закрыто, что нет и какие проекты показывать первыми."""
    advice = Advice(
        covered=report.covered,
        gaps=report.uncovered[:top_gaps],
        vacancies=report.current.vacancies,
    )

    frequency = report.current.terms_dict
    coverages: list[ProjectCoverage] = []
    for project in master.resume_projects:
        terms = sorted(
            {s.casefold() for s in project.stack} & set(frequency),
            key=lambda t: -frequency[t],
        )
        coverages.append(ProjectCoverage(project=project, terms=terms))

    coverages.sort(key=lambda c: (-c.weight, c.project.slug))
    advice.strongest = [c for c in coverages if c.weight]
    advice.unused_projects = [c.project.name or c.project.slug for c in coverages if not c.weight]
    return advice
