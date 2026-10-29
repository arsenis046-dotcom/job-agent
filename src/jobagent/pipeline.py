"""Оркестрация: search → score → tailor → apply.

Порядок и запреты зафиксированы здесь, чтобы отправка не могла случиться
в обход Fact Checker и лимитов. Шаги подключаются по фазам.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .agents.fact_checker import FactChecker
from .agents.letter import TemplateLetterWriter, build_letter_writer
from .agents.llm import LLMError, LLMRefused, LLMUnavailable
from .agents.matcher import APPLY, ASK, SKIP, Assessment, LocalMatcher, build_matcher
from .config import Config, Mode, env
from .connectors import ConnectorError, Vacancy, enabled_connectors
from .db import (
    already_applied,
    delete_vacancies,
    now,
    open_db,
    save_score,
    scored,
    sent_today,
    stale,
    unscored,
    upsert_vacancy,
)
from .documents import build_resume
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
    fallbacks: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        if not self.scored:
            return "оценивать нечего: новых вакансий нет"
        order = (APPLY, ASK, SKIP)
        counts = ", ".join(
            f"{name} {self.by_verdict.get(name, 0)}" for name in order
        )
        text = f"оценено {self.scored}: {counts}"
        if self.fallbacks:
            text += (
                f"\nоценено локально вместо модели: {len(self.fallbacks)}\n"
                + "\n".join(f"  - {note}" for note in self.fallbacks[:5])
            )
        return text


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


def run_score(config: Config, again: bool = False) -> ScoreOutcome:
    """Matcher: оценить всё, что ещё не оценивалось под активный профиль.

    `again=True` переоценивает и уже оценённое — после смены профиля поиска
    или движка. Прежние оценки остаются в истории.
    """
    outcome = ScoreOutcome()
    master = load_master_profile(config.root)
    matchers: dict[str, object] = {}
    local = LocalMatcher(config, master)

    def assess(vacancy: Vacancy) -> Assessment:
        """Модель там, где разрешено; при любой её осечке — локальная оценка."""
        if vacancy.platform not in matchers:
            matchers[vacancy.platform] = build_matcher(config, master, vacancy.platform)
        try:
            return matchers[vacancy.platform].assess(vacancy)
        except (LLMUnavailable, LLMRefused, LLMError) as exc:
            outcome.fallbacks.append(f"{vacancy.title}: {exc}")
            return local.assess(vacancy)

    conn = open_db(config.db_path)
    try:
        for row in unscored(conn, config.profile.name, again=again):
            assessment = assess(vacancy_from_row(row))
            _save(conn, row["id"], config.profile.name, assessment)
            outcome.scored += 1
            outcome.by_verdict[assessment.verdict] = (
                outcome.by_verdict.get(assessment.verdict, 0) + 1
            )
    finally:
        conn.close()
    return outcome


def vacancy_from_row(row) -> Vacancy:
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


@dataclass
class ApplyOutcome:
    budget: int = 0
    sent: int = 0
    drafts: int = 0
    awaiting: int = 0
    blocked: int = 0
    failed: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        parts = [
            f"дневной остаток лимита был {self.budget}",
            f"отправлено {self.sent}",
            f"черновиков {self.drafts}",
            f"ждут подтверждения {self.awaiting}",
            f"заблокировано проверкой фактов {self.blocked}",
        ]
        if self.failed:
            parts.append(f"ошибок отправки {self.failed}")
        if self.skipped:
            parts.append(f"пропущено {self.skipped}")
        text = ", ".join(parts)
        if self.notes:
            text += "\n" + "\n".join(f"  - {n}" for n in self.notes)
        return text


def _resume_id(config: Config, platform: str) -> str | None:
    """Идентификатор моего резюме на площадке. Без него отправлять нечего."""
    variable = {"superjob": "SUPERJOB_CV_ID", "hh": "HH_RESUME_ID"}.get(platform)
    return env(variable, config.root) if variable else None


def run_apply(config: Config, limit: int | None = None) -> ApplyOutcome:
    """Applier: письмо, обязательная проверка фактов, отправка в пределах лимита.

    Порядок неслучаен: пока резюме не прошло Fact Checker, не отправляется
    ничего и никуда. Проверка письма — для каждой вакансии отдельно, потому
    что письмо своё у каждой.
    """
    outcome = ApplyOutcome()
    profile = load_master_profile(config.root)
    resume = build_resume(profile)  # упадёт понятной ошибкой, если нет фактов

    checker = FactChecker(profile)
    resume_verdict = checker.check_resume(resume)
    if not resume_verdict.ok:
        outcome.notes.append(str(resume_verdict))
        return outcome

    writers: dict[str, object] = {}
    budget = daily_budget(config)
    if limit is not None:
        budget = min(budget, limit)
    outcome.budget = budget

    connectors = {c.name: c for c in enabled_connectors(config)}
    conn = open_db(config.db_path)
    try:
        for row in scored(conn, config.profile.name):
            if budget <= 0:
                outcome.notes.append("дневной лимит исчерпан, остальное — в другой раз")
                break
            if row["verdict"] != APPLY or row["applied"]:
                continue
            connector = connectors.get(row["platform"])
            if connector is None:
                outcome.skipped += 1
                continue

            vacancy = vacancy_from_row(row)
            if vacancy.platform not in writers:
                writers[vacancy.platform] = build_letter_writer(
                    config, profile, vacancy.platform
                )
            try:
                letter = writers[vacancy.platform].write(vacancy)
            except (LLMUnavailable, LLMRefused, LLMError) as exc:
                # письмо от модели не получилось — пишем шаблонное, но
                # обязательно отмечаем это: письмо будет суше
                outcome.notes.append(f"{row['title']}: письмо шаблонное ({exc})")
                letter = TemplateLetterWriter(profile).write(vacancy)
            letter_verdict = checker.check_letter(
                letter.text, " ".join(p for p in (row["title"], row["description"]) if p)
            )
            if not letter_verdict.ok:
                outcome.blocked += 1
                outcome.notes.append(f"{row['title']}: {letter_verdict}")
                _record(conn, row, config, resume, letter, status="blocked")
                continue

            result = connector.apply(
                vacancy,
                resume_id=_resume_id(config, row["platform"]) or "",
                message=letter.text,
            )
            _record(conn, row, config, resume, letter, status=result.status, result=result)

            if result.sent:
                outcome.sent += 1
                budget -= 1
            elif result.status == "draft":
                outcome.drafts += 1
                budget -= 1
            elif result.status == "awaiting":
                outcome.awaiting += 1
                budget -= 1
            elif result.status == "failed":
                outcome.failed += 1
            else:
                outcome.skipped += 1
            if result.detail:
                outcome.notes.append(f"{row['title']}: {result.detail}")
    finally:
        conn.close()
        for connector in connectors.values():
            connector.close()
    return outcome


def _record(conn, row, config: Config, resume, letter, *, status: str, result=None) -> None:
    """Для каждого отклика сохраняем точные тексты — чтобы можно было перечитать."""
    conn.execute(
        "INSERT INTO applications (vacancy_id, score_id, platform, mode, status, "
        "resume_text, letter_text, platform_response, error, created_at, sent_at) "
        "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row["id"],
            row["platform"],
            config.settings.mode.value,
            status,
            resume.text(),
            letter.text,
            json.dumps(result.response, ensure_ascii=False)
            if result is not None and result.response
            else None,
            result.detail if result is not None and status == "failed" else None,
            now(),
            now() if status == "sent" else None,
        ),
    )
    conn.commit()
