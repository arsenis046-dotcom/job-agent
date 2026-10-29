"""Живая проверка движков на модели: оценка, письмо, проверка фактов.

Запуск: `uv run python tools/live_check.py`

Что делает: берёт мой настоящий мастер-профиль и несколько вакансий, зовёт
модель по-настоящему и показывает, что она ответила. Письмо сразу прогоняется
через Fact Checker — то есть проверяется не «ответила ли модель», а
«пройдёт ли её ответ на отправку».

Вакансии здесь написаны от руки и помечены как придуманные: для проверки
промптов этого достаточно, а настоящую вакансию можно подложить файлом —
`uv run python tools/live_check.py вакансия.txt`, где в файле название
первой строкой и описание дальше.

Стоит денег: один запуск — примерно по два запроса на вакансию.
"""

from __future__ import annotations

import sys
from pathlib import Path

from jobagent.agents.fact_checker import FactChecker
from jobagent.agents.letter import ModelLetterWriter
from jobagent.agents.llm import LLM, LLMError, LLMRefused, LLMUnavailable
from jobagent.agents.matcher import ModelMatcher
from jobagent.config import load_config
from jobagent.connectors import Vacancy
from jobagent.profile import load_master_profile

# Придуманные вакансии: подходящая, пограничная и со стоп-фактором.
ПРИДУМАННЫЕ = [
    Vacancy(
        platform="superjob",
        external_id="demo-1",
        title="AI-разработчик (автоматизация внутренних процессов)",
        company="ООО Пример",
        salary_from=150000,
        salary_to=220000,
        description=(
            "Ищем разработчика для автоматизации внутренних процессов с помощью "
            "LLM. Задачи: писать сервисы на Python, собирать интеграции с "
            "внутренними системами по REST API, разворачивать в Docker. "
            "Требования: Python, Docker, PostgreSQL, опыт работы с API языковых "
            "моделей, умение доводить задачу до эксплуатации. Будет плюсом: "
            "FastAPI, Linux, опыт с Claude Code или Cursor. Удалённо."
        ),
    ),
    Vacancy(
        platform="superjob",
        external_id="demo-2",
        title="Python-разработчик в команду данных",
        company="ООО Пример",
        salary_from=200000,
        description=(
            "Требуется разработчик в команду дата-платформы. Обязанности: "
            "поддержка и развитие ETL на Airflow, работа с Kubernetes, "
            "написание Spark-джобов. Требования: Python, Airflow, Kubernetes, "
            "Spark, опыт от трёх лет в коммерческой разработке. Офис."
        ),
    ),
    Vacancy(
        platform="superjob",
        external_id="demo-3",
        title="Разработчик с ИИ",
        company="ООО Пример",
        salary_from=180000,
        description=(
            "Проект по автоматизации с использованием LLM, Python, Docker. "
            "Релокация обязательна, офис в другом городе."
        ),
    ),
]


def из_файла(path: Path) -> Vacancy:
    строки = path.read_text(encoding="utf-8").splitlines()
    return Vacancy(
        platform="superjob",
        external_id=path.stem,
        title=строки[0].strip(),
        description="\n".join(строки[1:]).strip() or None,
    )


def main() -> int:
    config = load_config()
    if not LLM(config).available:
        print("Нет ANTHROPIC_API_KEY — вписать в .env и повторить.")
        return 2

    profile = load_master_profile(config.root)
    matcher = ModelMatcher(config, profile)
    writer = ModelLetterWriter(config, profile)
    checker = FactChecker(profile)

    вакансии = [из_файла(Path(a)) for a in sys.argv[1:]] or ПРИДУМАННЫЕ
    if not sys.argv[1:]:
        print("Вакансии придуманные — проверяются промпты, а не выдача площадки.\n")
    print(f"Модель: {matcher.engine}, порог: {config.threshold}\n")

    for vacancy in вакансии:
        print("=" * 78)
        print(vacancy.title)
        try:
            оценка = matcher.assess(vacancy)
        except (LLMUnavailable, LLMRefused, LLMError) as exc:
            print(f"  оценка не получилась: {exc}")
            continue
        print(f"  оценка {оценка.score}, вердикт: {оценка.verdict}")
        print(f"  обоснование: {оценка.reasoning}")
        if оценка.matched:
            print(f"  совпало: {', '.join(оценка.matched)}")
        if оценка.gaps:
            print(f"  пробелы: {', '.join(оценка.gaps)}")
        if оценка.deal_breakers:
            print(f"  стоп-факторы: {', '.join(оценка.deal_breakers)}")

        if оценка.verdict != "apply":
            print("  письмо не пишем: вердикт не «откликаться»")
            continue

        try:
            письмо = writer.write(vacancy)
        except (LLMUnavailable, LLMRefused, LLMError) as exc:
            print(f"  письмо не получилось: {exc}")
            continue

        print(f"\n  --- письмо ({письмо.words} слов) ---")
        for строка in письмо.text.splitlines():
            print(f"  {строка}")

        текст_вакансии = " ".join(
            p for p in (vacancy.title, vacancy.description) if p
        )
        вердикт = checker.check_letter(письмо.text, текст_вакансии)
        print(f"\n  {вердикт}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
