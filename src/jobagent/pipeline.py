"""Оркестрация: search → score → tailor → apply.

Порядок и запреты зафиксированы здесь, чтобы отправка не могла случиться
в обход Fact Checker и лимитов. Шаги подключаются по фазам.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .agents.matcher import APPLY, ASK, SKIP, Assessment, LocalMatcher
from .config import Config, Mode
from .connectors import ConnectorError, Vacancy, enabled_connectors
from .db import (
    delete_vacancies,
    already_applied,
    open_db,
    save_score,
    sent_today,
    stale,
    unscored,
    upsert_vacancy,
)
from .profile import load_master_profile


@dataclass
class SearchOutcome:
    found: int = 0
    new: int = 0
    skipped_by_title: int = 0
    without_text: int = 0
    per_platform: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [
            f"найдено {self.found}, из них новых {self.new}",
            f"отсеяно по названию {self.skipped_by_title}",
            f"без описания {self.without_text}",
        ]
        if self.per_platform:
            parts.append(
                "по площадкам: "
                + ", ".join(f"{k} {v}" for k, v in sorted(self.per_platform.items()))
            )
        if self.errors:
            parts.append("ошибки: " + "; ".join(self.errors))
        return "\n".join(parts)


@dataclass
class ScoreOutcome:
    scored: int = 0
    by_verdict: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        if not self.scored:
            return "оценивать нечего: новых вакансий нет"
        order = (APPLY, ASK, SKIP)
        counts = ", ".join(
            f"{name} {self.by_verdict.get(name, 0)}" for name in order
        )
        return f"оценено {self.scored}: {counts}"


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
    return any(word.casefold() in title for word in config.profile.exclude_keywords)


def run_search(config: Config) -> SearchOutcome:
    """Scout: обойти площадки, отсеять заведомо лишнее, записать в базу.

    Ошибка одной площадки не останавливает остальные и не теряет уже
    найденное: исключение превращается в строку отчёта.
    """
    outcome = SearchOutcome()
    conn = open_db(config.db_path)
    try:
        for connector in enabled_connectors(config):
            count = 0
            try:
                for vacancy in connector.search():
                    outcome.found += 1
                    count += 1
                    if excluded_by_title(config, vacancy):
                        outcome.skipped_by_title += 1
                        continue
                    if not vacancy.has_text:
                        outcome.without_text += 1
                    known = conn.execute(
                        "SELECT 1 FROM vacancies WHERE platform = ? AND external_id = ?",
                        (vacancy.platform, vacancy.external_id),
                    ).fetchone()
                    if known is None:
                        outcome.new += 1
                    _store(conn, vacancy)
            except ConnectorError as exc:
                outcome.errors.append(str(exc))
            finally:
                connector.close()
            if count:
                outcome.per_platform[connector.name] = count
    finally:
        conn.close()
    return outcome


def _store(conn, vacancy: Vacancy) -> int:
    return upsert_vacancy(
        conn,
        platform=vacancy.platform,
        external_id=vacancy.external_id,
        url=vacancy.url,
        title=vacancy.title,
        company=vacancy.company,
        area=vacancy.area,
        salary_from=vacancy.salary_from,
        salary_to=vacancy.salary_to,
        currency=vacancy.currency,
        schedule=vacancy.schedule,
        employment=vacancy.employment,
        description=vacancy.description,
        published_at=vacancy.published_at,
        raw_json=json.dumps(vacancy.raw, ensure_ascii=False) if vacancy.raw else None,
    )


def run_score(config: Config) -> ScoreOutcome:
    """Matcher: оценить всё, что ещё не оценивалось под активный профиль."""
    outcome = ScoreOutcome()
    matcher = LocalMatcher(config, load_master_profile(config.root))
    conn = open_db(config.db_path)
    try:
        for row in unscored(conn, config.profile.name):
            assessment = matcher.assess(_vacancy_from_row(row))
            _save(conn, row["id"], config.profile.name, assessment)
            outcome.scored += 1
            outcome.by_verdict[assessment.verdict] = (
                outcome.by_verdict.get(assessment.verdict, 0) + 1
            )
    finally:
        conn.close()
    return outcome


def _vacancy_from_row(row) -> Vacancy:
    return Vacancy(
        platform=row["platform"],
        external_id=row["external_id"],
        title=row["title"],
        url=row["url"],
        company=row["company"],
        area=row["area"],
        salary_from=row["salary_from"],
        salary_to=row["salary_to"],
        currency=row["currency"],
        schedule=row["schedule"],
        employment=row["employment"],
        description=row["description"],
        published_at=row["published_at"],
    )


def _save(conn, vacancy_id: int, profile: str, assessment: Assessment) -> None:
    def dump(values: list[str]) -> str | None:
        return json.dumps(values, ensure_ascii=False) if values else None

    save_score(
        conn,
        vacancy_id,
        profile,
        score=assessment.score,
        verdict=assessment.verdict,
        matched=dump(assessment.matched),
        gaps=dump(assessment.gaps),
        deal_breakers=dump(assessment.deal_breakers),
        reasoning=assessment.reasoning,
        model=assessment.engine,
    )


@dataclass
class PruneOutcome:
    deleted: int = 0
    kept_applied: int = 0

    def __str__(self) -> str:
        text = f"удалено вакансий: {self.deleted}"
        if self.kept_applied:
            text += f"; оставлено с отправленным откликом: {self.kept_applied}"
        return text


def run_prune(config: Config, days: int = 14) -> PruneOutcome:
    """Убрать вакансии, которых давно нет в выдаче.

    П. 3.11 Условий API hh.ru требует следить за актуальностью данных и
    своевременно удалять архивные вакансии. Вакансии, на которые отклик уже
    отправлен, не удаляются: по ним хранится переписка и тексты писем.
    """
    outcome = PruneOutcome()
    conn = open_db(config.db_path)
    try:
        doomed: list[int] = []
        for row in stale(conn, days):
            if already_applied(conn, row["id"]):
                outcome.kept_applied += 1
                continue
            doomed.append(int(row["id"]))
        outcome.deleted = delete_vacancies(conn, doomed)
    finally:
        conn.close()
    return outcome


def run_apply(config: Config) -> None:
    """Фаза 3. Отправка возможна только после Fact Checker и в пределах лимита."""
    if config.settings.mode is Mode.auto:
        raise NotImplementedError(
            "Фаза 3: режим auto включается отдельной командой после подтверждения"
        )
    raise NotImplementedError("Фаза 3: Letter Writer, Fact Checker, Applier")
