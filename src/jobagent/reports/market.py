"""Рыночный отчёт, недельный дайджест и досье к собеседованию — в output/."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..agents.interview import Dossier
from ..agents.market import MarketReport, analyze, save_snapshot
from ..agents.portfolio import Advice, advise
from ..config import Config
from ..db import open_db, sent_today
from ..profile import load_master_profile

ОГОВОРКА = (
    "Термины извлечены из текстов вакансий автоматически: словаря технологий "
    "здесь нет, за термин берётся латинское слово плюс многословные названия "
    "из моего профиля. Это приближение, а не справочник навыков."
)


def _money(value: int | None) -> str:
    return "—" if value is None else f"{value:,}".replace(",", " ")


def вакансий(count: int) -> str:
    """«в 1 вакансии», «в 2 вакансиях», «в 5 вакансиях» — без «в 1 вакансиях»."""
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} вакансии"
    return f"{count} вакансиях"


def build(config: Config) -> tuple[MarketReport, Advice]:
    """Считает рынок и советы по портфолио, сохраняя снимок для динамики."""
    master = load_master_profile(config.root)
    conn = open_db(config.db_path)
    try:
        report = analyze(conn, config.profile.name, master)
        save_snapshot(conn, report.current)
    finally:
        conn.close()
    return report, advise(report, master)


def write_market_report(config: Config, day: date | None = None) -> Path:
    day = day or date.today()
    report, advice = build(config)
    snapshot = report.current

    lines = [
        f"# Рынок по профилю «{snapshot.profile}»",
        "",
        f"Замер от {day.isoformat()}. Вакансий в базе: {snapshot.vacancies}.",
    ]
    delta = report.vacancies_delta
    if delta is not None:
        знак = "+" if delta > 0 else ""
        lines.append(
            f"С прошлого замера ({report.previous.taken_at[:10]}): {знак}{delta}."
        )
    lines += [
        "",
        "## Зарплаты",
        "",
        f"Вилка указана у {snapshot.with_salary} вакансий из {snapshot.vacancies}.",
        "",
        "| Квартиль | Значение |",
        "|---|---|",
        f"| 25% | {_money(snapshot.salary_p25)} |",
        f"| медиана | {_money(snapshot.salary_median)} |",
        f"| 75% | {_money(snapshot.salary_p75)} |",
        "",
        "## Что чаще всего просят",
        "",
        ОГОВОРКА,
        "",
        "| Термин | В скольких вакансиях | Есть у меня |",
        "|---|---|---|",
    ]
    mine = {t for t, _ in advice.covered}
    for term, count in snapshot.terms:
        lines.append(f"| {term} | {count} | {'да' if term in mine else 'нет'} |")

    if report.growing():
        lines += ["", "## Чего стало больше", ""]
        lines += [f"- {term}: +{delta}" for term, delta in report.growing()]
    if report.falling():
        lines += ["", "## Чего стало меньше", ""]
        lines += [f"- {term}: {delta}" for term, delta in report.falling()]

    lines += ["", "## Что подтянуть", ""]
    if advice.coverage_percent is not None:
        lines.append(
            f"Из частых требований моими навыками закрыто {advice.coverage_percent}%."
        )
        lines.append("")
    if advice.gaps:
        lines += [f"- {term} — в {вакансий(count)}" for term, count in advice.gaps]
    else:
        lines.append("Пробелов среди частых требований не нашлось.")

    if advice.strongest:
        lines += ["", "## Какие проекты показывать первыми", ""]
        for coverage in advice.strongest:
            name = coverage.project.name or coverage.project.slug
            lines.append(f"- **{name}** — попадает в {', '.join(coverage.terms)}")
    if advice.unused_projects:
        lines += [
            "",
            "Проекты, стек которых в этих вакансиях не встретился: "
            + ", ".join(advice.unused_projects)
            + ".",
        ]

    path = config.output_dir / f"рынок-{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def write_digest(config: Config, day: date | None = None) -> Path:
    """Недельный дайджест: что произошло и что требует моего внимания."""
    day = day or date.today()
    conn = open_db(config.db_path)
    try:
        счёт = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
        всего = счёт("SELECT COUNT(*) FROM vacancies")
        неделя = счёт(
            "SELECT COUNT(*) FROM vacancies WHERE found_at >= datetime('now', '-7 days')"
        )
        к_отклику = счёт(
            "SELECT COUNT(*) FROM scores s WHERE s.profile = ? AND s.verdict = 'apply' "
            "AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.vacancy_id = s.vacancy_id "
            "AND a.status = 'sent')",
            config.profile.name,
        )
        спросить = счёт(
            "SELECT COUNT(*) FROM scores WHERE profile = ? AND verdict = 'ask'",
            config.profile.name,
        )
        отправлено = счёт(
            "SELECT COUNT(*) FROM applications WHERE status = 'sent' "
            "AND sent_at >= datetime('now', '-7 days')"
        )
        заблокировано = счёт(
            "SELECT COUNT(*) FROM applications WHERE status = 'blocked'"
        )
        сегодня = sent_today(conn)
    finally:
        conn.close()

    lines = [
        f"# Дайджест за неделю, {day.isoformat()}",
        "",
        f"Профиль поиска: «{config.profile.name}», режим: {config.settings.mode.value}.",
        "",
        "| Что | Сколько |",
        "|---|---|",
        f"| вакансий в базе | {всего} |",
        f"| найдено за неделю | {неделя} |",
        f"| ждут отклика | {к_отклику} |",
        f"| требуют моего решения | {спросить} |",
        f"| откликов отправлено за неделю | {отправлено} |",
        f"| отправок заблокировано проверкой фактов | {заблокировано} |",
        f"| отправлено сегодня (лимит {config.settings.limits.applications_per_day}) | {сегодня} |",
        "",
    ]
    if заблокировано:
        lines.append(
            "**Заблокированные отправки стоит разобрать**: это расхождение между "
            "письмом и мастер-профилем, а не сбой."
        )
        lines.append("")
    if спросить:
        lines.append(
            f"{спросить} вакансий агент оценить не смог и ждёт меня — "
            "обычно это вакансии без описания или на незаполненном профиле поиска."
        )
        lines.append("")

    path = config.output_dir / f"дайджест-{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return path


def write_dossier(config: Config, dossier: Dossier, vacancy_id: int) -> Path:
    lines = [
        f"# К собеседованию: {dossier.vacancy_title}",
        "",
        f"Компания: {dossier.company or 'не указана'}."
        + (f" Оценка соответствия: {dossier.score}." if dossier.score is not None else ""),
    ]
    if dossier.url:
        lines.append(f"Вакансия: {dossier.url}")
    lines += ["", "## Что у меня совпадает", ""]
    lines.append(
        ", ".join(dossier.matched) if dossier.matched else "Явных совпадений не нашлось."
    )
    lines += ["", "## О чём спросят, а я не говорил, что умею", ""]
    lines.append(
        ", ".join(dossier.to_learn[:20])
        if dossier.to_learn
        else "Незнакомых терминов в вакансии нет."
    )
    lines += [
        "",
        "Это не список того, что надо срочно выучить: часть слов окажется "
        "названием их продукта или общими словами. Но пробежаться стоит.",
        "",
        "## Про отсутствие коммерческого опыта",
        "",
        dossier.honest_answer,
        "",
        "## Какие проекты рассказывать",
        "",
    ]
    for project in dossier.projects:
        lines.append(f"### {project.name or project.slug}")
        lines.append("")
        if project.one_liner:
            lines.append(f"{project.one_liner}.")
        if project.problem:
            lines.append(f"Задача: {project.problem}")
        if project.my_role:
            lines.append(f"Моя роль: {project.my_role}")
        if project.result:
            lines.append(f"Результат: {project.result}")
        if project.stack:
            lines.append(f"Стек: {', '.join(project.stack)}")
        if project.link:
            lines.append(f"Ссылка: {project.link}")
        lines.append("")

    lines += ["## Вопросы работодателю", ""]
    lines += [f"- {q}" for q in dossier.questions]
    lines += ["", "## Чек-лист на день собеседования", ""]
    lines += [f"- [ ] {item}" for item in dossier.checklist]

    path = config.output_dir / f"собеседование-{vacancy_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path
