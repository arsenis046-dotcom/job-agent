"""Отчёт по найденным вакансиям: таблица, которую можно прочитать глазами.

Два файла рядом: Markdown — читать, CSV — сортировать в таблице. Секретов
в отчёт не попадает, в нём только то, что отдала площадка.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from ..config import Config
from ..db import open_db, scored

VERDICT_RU = {"apply": "откликаться", "ask": "спросить", "skip": "пропустить"}

COLUMNS = (
    "оценка",
    "вердикт",
    "вакансия",
    "компания",
    "зарплата",
    "город",
    "площадка",
    "ссылка",
)


def salary_text(row) -> str:
    low, high, currency = row["salary_from"], row["salary_to"], row["currency"] or ""
    currency = {"rub": "₽", "RUR": "₽", "RUB": "₽"}.get(currency, currency)
    if low and high:
        return f"{low:,}–{high:,} {currency}".replace(",", " ").strip()
    if low:
        return f"от {low:,} {currency}".replace(",", " ").strip()
    if high:
        return f"до {high:,} {currency}".replace(",", " ").strip()
    return "не указана"


def _row_values(row) -> list[str]:
    return [
        "—" if row["score"] is None else str(row["score"]),
        VERDICT_RU.get(row["verdict"], row["verdict"]),
        row["title"],
        row["company"] or "—",
        salary_text(row),
        row["area"] or "—",
        row["platform"],
        row["url"] or "—",
    ]


def write_report(config: Config, day: date | None = None) -> tuple[Path, Path]:
    """Складывает отчёт в output/. Возвращает пути к Markdown и CSV."""
    day = day or date.today()
    folder = config.output_dir
    folder.mkdir(parents=True, exist_ok=True)
    md_path = folder / f"вакансии-{day.isoformat()}.md"
    csv_path = folder / f"вакансии-{day.isoformat()}.csv"

    conn = open_db(config.db_path)
    try:
        rows = scored(conn, config.profile.name)
    finally:
        conn.close()

    lines = [
        f"# Вакансии по профилю «{config.profile.name}»",
        "",
        f"Отчёт от {day.isoformat()}. Порог оценки {config.threshold}. "
        f"Всего оценено: {len(rows)}.",
        "",
    ]
    if not rows:
        lines += [
            "Оценённых вакансий пока нет. Сначала `jobagent search`, "
            "затем `jobagent score`.",
            "",
        ]
    else:
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
        lines.append(
            "Итого: "
            + ", ".join(
                f"{VERDICT_RU.get(k, k)} — {v}" for k, v in sorted(counts.items())
            )
            + "."
        )
        lines += ["", "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
        for row in rows:
            values = [v.replace("|", "\\|") for v in _row_values(row)]
            lines.append("| " + " | ".join(values) + " |")
        lines += ["", "## Чем считали", ""]
        for row in rows:
            matched = json.loads(row["matched"]) if row["matched"] else []
            gaps = json.loads(row["gaps"]) if row["gaps"] else []
            lines.append(f"**{row['title']}** — {row['reasoning']}")
            if matched:
                lines.append(f"- совпало: {', '.join(matched)}")
            if gaps:
                lines.append(f"- пробелы: {', '.join(gaps)}")
            lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow(_row_values(row))

    return md_path, csv_path
