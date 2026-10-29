"""Проверка, что ATS прочитает резюме так же, как человек.

Способ честный и простой: извлечь из готового PDF текстовый слой и убедиться,
что в нём есть все заголовки в том же порядке, что в документе, и все факты
из каждой строки. Если одноколоночная вёрстка где-то поехала или шрифт
не отдал текст, порядок сломается — и проверка это покажет.

Никаких приёмов против ATS мы не используем, так что проверка нужна не для
обхода, а чтобы убедиться: резюме читается.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .resume import Resume

WHITESPACE = re.compile(r"\s+")


@dataclass
class AtsReport:
    chars: int = 0
    problems: list[str] = field(default_factory=list)
    checked_lines: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def __str__(self) -> str:
        if self.ok:
            return (
                f"ATS: текстовый слой читается, {self.chars} символов, "
                f"{self.checked_lines} строк на месте, порядок разделов верный"
            )
        return "ATS: проблемы\n" + "\n".join(f"  - {p}" for p in self.problems)


def extract_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def normalize(text: str) -> str:
    """Сжимаем пробелы: при извлечении строки переносятся, и это нормально."""
    return WHITESPACE.sub(" ", text.replace("­", "").replace("‑", "-")).strip()


def check_pdf(path: Path, resume: Resume) -> AtsReport:
    report = AtsReport()
    text = normalize(extract_text(path))
    report.chars = len(text)

    if report.chars < 100:
        report.problems.append(
            "текстового слоя почти нет — вероятно, резюме превратилось в картинку"
        )
        return report

    if normalize(resume.name.text) not in text:
        report.problems.append("имя не читается из текстового слоя")

    # заголовки: присутствуют и идут в том же порядке, что в документе
    position = -1
    for heading in resume.headings:
        found = text.find(normalize(heading), position + 1)
        if found == -1:
            report.problems.append(f"заголовок «{heading}» не найден в тексте PDF")
            continue
        if found <= position:
            report.problems.append(f"заголовок «{heading}» идёт не в своём порядке")
        position = found

    # факты: каждый, из которого собрана строка, должен читаться
    for line in resume.all_lines:
        report.checked_lines += 1
        for fact in line.evidence:
            if normalize(fact) not in text:
                report.problems.append(
                    f"{line.source}: факт не читается из PDF — «{normalize(fact)[:60]}»"
                )

    return report
