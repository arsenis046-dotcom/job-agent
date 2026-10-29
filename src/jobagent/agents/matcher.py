"""Matcher — оценка вакансии на соответствие профилю.

Два движка с одним интерфейсом:

- `LocalMatcher` — без модели, по ключевым словам и моим навыкам. Нужен там,
  где отправлять текст вакансии наружу нельзя: п. 4.6 Условий API hh.ru
  запрещает передавать полученные данные сторонним сервисам, а модель —
  сторонний сервис (разбор в `docs/hh-application.md`).
- движок на модели появится рядом и будет выбираться по площадке.

Локальная оценка нарочно скромная и честная: она считает совпадения слов,
а не понимает текст. Поэтому у неё есть вердикт `ask` — «спроси меня», и в
обоснование всегда пишется, чем именно считали.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import Config
from ..connectors import Vacancy
from ..profile import MasterProfile

APPLY = "apply"
SKIP = "skip"
ASK = "ask"

# Насколько ниже порога вакансия ещё достойна вопроса, а не отказа
ASK_MARGIN = 15

# Потолок оценки, когда в профиле поиска нечем различать вакансии
WEAK_CEILING = 75


@dataclass
class Assessment:
    score: int | None
    verdict: str
    reasoning: str
    engine: str
    matched: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    deal_breakers: list[str] = field(default_factory=list)


def _contains(text: str, needle: str) -> bool:
    """Поиск слова, а не подстроки: «ML» не должно находиться внутри «HTML»."""
    needle = needle.strip().casefold()
    if not needle:
        return False
    pattern = r"(?<![\w-])" + re.escape(needle) + r"(?![\w-])"
    return re.search(pattern, text, re.IGNORECASE) is not None


class LocalMatcher:
    """Оценка без обращения к модели."""

    engine = "local"

    def __init__(self, config: Config, profile: MasterProfile) -> None:
        self.config = config
        self.search = config.profile
        self.my_skills = [
            skill for _, items in profile.skills.groups() for skill in items
        ]

    def assess(self, vacancy: Vacancy) -> Assessment:
        haystack = " ".join(
            part for part in (vacancy.title, vacancy.description) if part
        ).casefold()

        breakers = [
            phrase
            for phrase in self.search.deal_breakers
            if phrase.strip() and phrase.casefold() in haystack
        ]
        if breakers:
            return Assessment(
                score=0,
                verdict=SKIP,
                reasoning="стоп-фактор в тексте вакансии",
                engine=self.engine,
                deal_breakers=breakers,
            )

        excluded = [
            word
            for word in self.search.exclude_keywords
            if _contains(vacancy.title.casefold(), word)
        ]
        if excluded:
            return Assessment(
                score=0,
                verdict=SKIP,
                reasoning=f"в названии исключающие слова: {', '.join(excluded)}",
                engine=self.engine,
                gaps=excluded,
            )

        if not vacancy.has_text:
            return Assessment(
                score=None,
                verdict=ASK,
                reasoning=(
                    "описания вакансии нет — оценивать нечем. "
                    "Для hh.ru это ожидаемо, пока приложение не одобрено"
                ),
                engine=self.engine,
            )

        parts: list[tuple[float, float]] = []  # (значение 0..1, вес)
        matched: list[str] = []
        gaps: list[str] = []

        keywords = [k for k in self.search.keywords if _contains(haystack, k)]
        matched.extend(keywords)
        parts.append((min(len(keywords), 2) / 2, 30))

        skills = [s for s in self.my_skills if _contains(haystack, s)]
        matched.extend(skills)
        parts.append((min(len(skills), 3) / 3, 30))

        if self.search.must_have_skills:
            found = [s for s in self.search.must_have_skills if _contains(haystack, s)]
            gaps.extend(s for s in self.search.must_have_skills if s not in found)
            parts.append((len(found) / len(self.search.must_have_skills), 25))

        if self.search.nice_to_have_skills:
            found = [
                s for s in self.search.nice_to_have_skills if _contains(haystack, s)
            ]
            matched.extend(found)
            parts.append((len(found) / len(self.search.nice_to_have_skills), 10))

        if self.search.salary_min:
            top = vacancy.salary_to or vacancy.salary_from
            if top:
                parts.append((1.0 if top >= self.search.salary_min else 0.0, 15))
                if top < self.search.salary_min:
                    gaps.append(f"зарплата ниже {self.search.salary_min}")

        if self.search.schedule:
            fits = any(
                _contains(haystack, item) or _contains(vacancy.schedule or "", item)
                for item in self.search.schedule
            )
            parts.append((1.0 if fits else 0.0, 15))
            if not fits:
                gaps.append("формат работы не подтверждён")

        weight = sum(w for _, w in parts)
        score = round(100 * sum(v * w for v, w in parts) / weight) if weight else 0

        # Ключевые слова и навыки есть всегда, и на них одних легко набрать 100
        # за два совпавших слова. Если в профиле поиска нет ничего
        # различающего — зарплаты, формата, обязательных навыков, — считать
        # толком нечем, и честнее сказать «спроси меня», чем «откликайся».
        weak = len(parts) <= 2
        threshold = self.config.threshold
        if weak:
            score = min(score, WEAK_CEILING)

        if score >= threshold and not weak:
            verdict = APPLY
        elif score >= threshold - ASK_MARGIN:
            verdict = ASK
        else:
            verdict = SKIP

        reasoning = (
            f"локальная оценка по словам: совпало ключевых слов {len(keywords)}, "
            f"моих навыков {len(skills)}; порог {threshold}"
        )
        if weak:
            reasoning += (
                ". В профиле поиска не заданы обязательные навыки, зарплата и "
                f"формат работы — оценка ненадёжна и ограничена {WEAK_CEILING}"
            )

        return Assessment(
            score=score,
            verdict=verdict,
            reasoning=reasoning,
            engine=self.engine,
            matched=sorted(set(matched)),
            gaps=sorted(set(gaps)),
        )
