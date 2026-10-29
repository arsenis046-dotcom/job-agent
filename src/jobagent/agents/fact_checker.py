"""Fact Checker — обязательная проверка перед отправкой.

Сверяет каждое утверждение резюме и письма с мастер-профилем и блокирует
отправку при расхождении. Работает без модели: там, где проверка должна
что-то запрещать, она не может сама быть предположением.

Два разрешённых источника:

- **мастер-профиль** — для всего, что сказано обо мне;
- **текст вакансии** — для упоминаний работодателя: название компании,
  их стек, слова из их объявления. ТЗ прямо просит конкретику из вакансии,
  и запрещать её было бы неправильно.

Утверждение, которого нет ни там, ни там, — выдумка, и отправка блокируется.
Резюме проверяется иначе, чем письмо: у каждой его строки есть
происхождение (`Line.evidence`), поэтому для резюме проверка строгая —
совпадение факта целиком.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..documents.resume import Resume
from ..profile import MasterProfile

# Числа: «0.92», «70», «2026», «120 000». Самое опасное в резюме — цифры.
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
URL = re.compile(r"https?://\S+")
# Латиница как признак названия технологии или продукта
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}")

# Короткие латинские обрывки вроде «ru» из адресов проверять бессмысленно
MIN_LATIN = 2


@dataclass
class Problem:
    claim: str
    kind: str  # число / ссылка / название
    where: str

    def __str__(self) -> str:
        return f"{self.where}: {self.kind} «{self.claim}» нет ни в профиле, ни в вакансии"


@dataclass
class Verdict:
    ok: bool
    checked: int = 0
    problems: list[Problem] = field(default_factory=list)

    def __str__(self) -> str:
        if self.ok:
            return f"Fact Checker: проверено утверждений {self.checked}, расхождений нет"
        return "Fact Checker: ОТПРАВКА ЗАБЛОКИРОВАНА\n" + "\n".join(
            f"  - {p}" for p in self.problems
        )


def _strings(value: Any) -> list[str]:
    """Все строковые значения вложенной структуры."""
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    if value is None or isinstance(value, bool):
        return []
    return [str(value)]


class FactChecker:
    def __init__(self, profile: MasterProfile) -> None:
        self.profile = profile
        self.facts = [s.strip() for s in _strings(profile.model_dump(mode="json"))]
        self.haystack = " ".join(self.facts).casefold()

    # --- резюме ------------------------------------------------------------

    def check_resume(self, resume: Resume) -> Verdict:
        """Строгая проверка: каждый факт строки должен быть в профиле целиком."""
        verdict = Verdict(ok=True)
        allowed = {f.casefold() for f in self.facts}
        for line in resume.all_lines:
            for fact in line.evidence:
                verdict.checked += 1
                if fact.strip().casefold() not in allowed:
                    verdict.ok = False
                    verdict.problems.append(
                        Problem(claim=fact, kind="факт", where=f"резюме, {line.source}")
                    )
        return verdict

    # --- письмо ------------------------------------------------------------

    def check_letter(self, text: str, vacancy_text: str = "") -> Verdict:
        """Проверка свободного текста: числа, ссылки и названия.

        Письмо может быть написано моделью, поэтому проверяется то, что
        модель способна выдумать и что дороже всего стоит: цифры, ссылки
        и названия технологий или компаний.
        """
        verdict = Verdict(ok=True)
        vacancy = vacancy_text.casefold()

        def known(claim: str) -> bool:
            needle = claim.casefold()
            return needle in self.haystack or needle in vacancy

        for pattern, kind in ((URL, "ссылка"), (NUMBER, "число"), (LATIN, "название")):
            for match in pattern.finditer(text):
                claim = match.group(0).strip(".,;:!?()«»\"'")
                if not claim or (kind == "название" and len(claim) < MIN_LATIN):
                    continue
                verdict.checked += 1
                if not known(claim):
                    verdict.ok = False
                    verdict.problems.append(
                        Problem(claim=claim, kind=kind, where="письмо")
                    )
        return verdict


def merge(*verdicts: Verdict) -> Verdict:
    """Общий вердикт: блокирует, если блокирует хоть одна проверка."""
    result = Verdict(ok=True)
    for verdict in verdicts:
        result.checked += verdict.checked
        result.problems.extend(verdict.problems)
    result.ok = not result.problems
    return result
