"""Оркестрация: search → score → tailor → apply.

Фаза 0: здесь только каркас и проверка предварительных условий. Шаги
подключаются по фазам, но порядок и запреты зафиксированы сразу, чтобы
отправка не могла случиться в обход Fact Checker и лимитов.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, Mode
from .connectors import Connector, Vacancy, enabled_connectors
from .db import open_db, sent_today


@dataclass
class SearchOutcome:
    found: int = 0
    new: int = 0
    without_text: int = 0
    errors: list[str] = field(default_factory=list)


def daily_budget(config: Config) -> int:
    """Сколько откликов ещё можно отправить сегодня."""
    limit = config.settings.limits.applications_per_day
    conn = open_db(config.db_path)
    try:
        return max(0, limit - sent_today(conn))
    finally:
        conn.close()


def excluded_by_title(config: Config, vacancy: Vacancy) -> bool:
    """Дешёвый отсев до обращения к модели."""
    title = vacancy.title.casefold()
    return any(
        word.casefold() in title for word in config.profile.exclude_keywords
    )


def run_search(config: Config) -> SearchOutcome:
    """Фаза 2. Пока только собирает вакансии в базу, без оценки."""
    outcome = SearchOutcome()
    connectors: list[Connector] = enabled_connectors(config)
    raise NotImplementedError(
        "Фаза 2: Scout подключается после согласования режимов доступа "
        f"(включено площадок: {len(connectors)})"
    )


def run_apply(config: Config) -> None:
    """Фаза 3. Отправка возможна только после Fact Checker и в пределах лимита."""
    if config.settings.mode is Mode.auto:
        raise NotImplementedError(
            "Фаза 3: режим auto включается отдельной командой после подтверждения"
        )
    raise NotImplementedError("Фаза 3: Letter Writer, Fact Checker, Applier")
