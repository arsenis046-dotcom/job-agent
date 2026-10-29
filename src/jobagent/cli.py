"""CLI. Команды появляются по фазам; нереализованные честно об этом говорят."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import (
    Config,
    ConfigError,
    Mode,
    available_profiles,
    create_profile,
    env,
    load_config,
    load_profile,
    set_active_profile,
    set_mode,
)
from .agents.sphere import relevance
from .connectors import REGISTRY, ConnectorError, build_connector
from .db import SCHEMA_VERSION, open_db, sent_today
from .documents import (
    RenderError,
    ResumeError,
    build_resume,
    check_pdf,
    render_docx,
    render_pdf,
)
from .agents.interview import prepare
from .pipeline import (
    run_apply,
    run_prune,
    run_score,
    run_search,
    vacancy_from_row,
)
from .profile import find_gaps, load_master_profile
from .reports.market import write_digest, write_dossier, write_market_report
from .reports.vacancies import write_report

console = Console()

app = typer.Typer(
    help="Job Agent — личный ассистент по поиску работы.",
    no_args_is_help=True,
    add_completion=False,
)
profile_app = typer.Typer(help="Профили поиска и мастер-профиль.", no_args_is_help=True)
app.add_typer(profile_app, name="profile")
telegram_app = typer.Typer(
    help="Чтение публичных каналов с вакансиями.", no_args_is_help=True
)
app.add_typer(telegram_app, name="telegram")


def _config() -> Config:
    try:
        return load_config()
    except ConfigError as exc:
        console.print(f"[red]Конфигурация:[/red] {exc}")
        raise typer.Exit(2) from exc


def _phase(number: int, what: str) -> None:
    console.print(f"[yellow]Фаза {number}[/yellow]: {what} — ещё не реализовано.")
    raise typer.Exit(1)


@app.command()
def version() -> None:
    """Версия."""
    console.print(f"job-agent {__version__}, схема БД v{SCHEMA_VERSION}")


@app.command()
def init() -> None:
    """Создать базу и проверить, что конфигурация читается."""
    config = _config()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    conn = open_db(config.db_path)
    conn.close()
    console.print(f"База: [green]{config.db_path}[/green]")
    console.print(f"Активный профиль: [green]{config.profile.name}[/green]")
    console.print(f"Режим: [green]{config.settings.mode.value}[/green]")


@app.command()
def doctor() -> None:
    """Что настроено, что нет и чего ждать от каждой площадки."""
    config = _config()
    settings = config.settings

    table = Table(title="Настройки", show_header=False)
    table.add_row("режим", settings.mode.value)
    table.add_row("профиль поиска", config.profile.name)
    table.add_row("порог оценки", str(config.threshold))
    table.add_row("лимит откликов в сутки", str(settings.limits.applications_per_day))
    table.add_row("модель", settings.llm.model)
    console.print(table)

    secrets = Table(title="Секреты (.env)")
    secrets.add_column("переменная")
    secrets.add_column("есть")
    for name in (
        "ANTHROPIC_API_KEY",
        "SUPERJOB_CLIENT_ID",
        "SUPERJOB_SECRET",
        "SUPERJOB_LOGIN",
        "SUPERJOB_PASSWORD",
        "HH_ACCESS_TOKEN",
        "CONTACT_EMAIL",
    ):
        # печатаем только факт наличия, никогда значение
        secrets.add_row(name, "да" if env(name, config.root) else "[red]нет[/red]")
    console.print(secrets)

    platforms = Table(title="Площадки")
    for column in ("площадка", "включена", "доступ", "отклик", "коннектор"):
        platforms.add_column(column)
    for name, platform in settings.platforms.items():
        platforms.add_row(
            name,
            "да" if platform.enabled else "нет",
            platform.access.value,
            platform.apply.value,
            "есть" if name in REGISTRY else "[yellow]нет[/yellow]",
        )
    console.print(platforms)

    if config.db_path.exists():
        conn = open_db(config.db_path)
        console.print(f"Откликов отправлено сегодня: {sent_today(conn)}")
        conn.close()
    else:
        console.print("Базы пока нет — выполнить [green]jobagent init[/green]")


@profile_app.command("list")
def profile_list() -> None:
    """Какие профили поиска есть."""
    config = _config()
    for name in available_profiles(config.root):
        mark = "*" if name == config.settings.active_profile else " "
        console.print(f"{mark} {name}")


@profile_app.command("show")
def profile_show() -> None:
    """Активный профиль поиска целиком."""
    config = _config()
    for key, value in config.profile.model_dump().items():
        console.print(f"[cyan]{key}[/cyan]: {value}")


@profile_app.command("switch")
def profile_switch(name: str) -> None:
    """Сменить активный профиль поиска. Код при этом не меняется."""
    config = _config()
    try:
        set_active_profile(name, config.root)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(f"Активный профиль: [green]{name}[/green]")


@profile_app.command("new")
def profile_new(
    name: str = typer.Argument(..., help="Имя файла: строчные латинские буквы"),
    keyword: list[str] = typer.Option(
        None, "--keyword", "-k", help="Ключевое слово для поиска, можно несколько"
    ),
    title: str = typer.Option(None, help="Название сферы по-русски"),
    threshold: int = typer.Option(70, help="Порог оценки для этой сферы"),
    switch: bool = typer.Option(False, "--switch", help="Сразу сделать активным"),
) -> None:
    """Создать профиль поиска под новую сферу. Правок в коде не требует."""
    config = _config()
    keywords = list(keyword or [])
    if not keywords:
        console.print("Ключевые слова, по которым искать. Пустая строка — закончить.")
        while True:
            answer = typer.prompt(f"  слово {len(keywords) + 1}", default="", show_default=False)
            if not answer.strip():
                break
            keywords.append(answer.strip())

    try:
        path = create_profile(
            name, keywords, title=title, threshold=threshold, root=config.root
        )
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(f"Профиль: [green]{path}[/green]")

    # сразу говорим, с чем мы в эту сферу идём
    try:
        master = load_master_profile(config.root)
    except ConfigError:
        console.print(
            "[yellow]Мастер-профиль не заполнен — что переносится в новую сферу, "
            "сказать пока нечем.[/yellow]"
        )
    else:
        console.print(str(relevance(load_profile(name, config.root), master)))

    if switch:
        set_active_profile(name, config.root)
        console.print(f"Активный профиль: [green]{name}[/green]")
    else:
        console.print(f"Сделать активным: [green]jobagent profile switch {name}[/green]")


@profile_app.command("check")
def profile_check() -> None:
    """Что в мастер-профиле заполнено, а чего не хватает для резюме."""
    config = _config()
    try:
        master = load_master_profile(config.root)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    gaps = find_gaps(master)
    blocking = [gap for gap in gaps if gap.blocking]

    console.print(f"Проектов в профиле: {len(master.projects)}, "
                  f"годных для резюме: {len(master.resume_projects)}")
    console.print(f"Навыков: {master.skills.total}")

    if not gaps:
        console.print("[green]Профиль заполнен полностью.[/green]")
        return
    for gap in gaps:
        color = "red" if gap.blocking else "yellow"
        console.print(f"[{color}]{gap}[/{color}]")
    if blocking:
        console.print(
            f"\n[red]Резюме пока собрать нельзя: {len(blocking)} обязательных "
            f"фактов не хватает.[/red]"
        )
        raise typer.Exit(1)
    console.print("\n[green]Резюме собрать можно, остальное — на твоё усмотрение.[/green]")


@profile_app.command("build")
def profile_build() -> None:
    """Задать вопросы по незаполненным фактам и записать ответы в профиль.

    Диалоговый вариант этого же — субагент `.claude/agents/profile-builder.md`;
    он удобнее для проектов, где нужен разговор, а не одно поле.
    """
    config = _config()
    master = load_master_profile(config.root)
    gaps = find_gaps(master)
    if not gaps:
        console.print("[green]Нечего спрашивать: профиль заполнен.[/green]")
        return

    console.print(
        "Пустой ответ — пропустить вопрос. Выдумывать за тебя я не буду.\n"
    )
    answers: dict[str, str] = {}
    for gap in gaps:
        mark = "обязательно" if gap.blocking else "можно пропустить"
        answer = typer.prompt(f"{gap.question} ({mark})", default="", show_default=False)
        if answer.strip():
            answers[gap.path] = answer.strip()

    if not answers:
        console.print("Ответов нет, профиль не изменился.")
        return
    console.print(
        f"\nПолучено ответов: {len(answers)}. "
        "Поля со списками и проекты я не трогаю автоматически — "
        "их правильнее заполнить через субагента или руками:"
    )
    for path, answer in answers.items():
        console.print(f"  [cyan]{path}[/cyan] = {answer}")


@app.command()
def resume(
    out: Path = typer.Option(None, help="Куда положить файлы (по умолчанию output/)"),
    skip_pdf: bool = typer.Option(False, "--skip-pdf", help="Только DOCX"),
) -> None:
    """Собрать базовое резюме из мастер-профиля: DOCX, PDF и проверка ATS."""
    config = _config()
    try:
        master = load_master_profile(config.root)
        document = build_resume(master)
    except (ConfigError, ResumeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    folder = out or config.output_dir
    stem = (master.person.full_name or "resume").replace(" ", "_")
    docx_path = render_docx(document, folder / f"{stem}.docx")
    console.print(f"DOCX: [green]{docx_path}[/green]")

    if skip_pdf:
        return
    try:
        pdf_path = render_pdf(document, folder / f"{stem}.pdf")
    except RenderError as exc:
        console.print(f"[yellow]PDF не собран:[/yellow] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"PDF:  [green]{pdf_path}[/green]")

    report = check_pdf(pdf_path, document)
    console.print(str(report) if report.ok else f"[red]{report}[/red]")
    if not report.ok:
        raise typer.Exit(1)


@telegram_app.command("login")
def telegram_login() -> None:
    """Войти в Telegram под своим аккаунтом. Один раз.

    Телефон и код спрашивает сам Telethon, и вводишь их ты: ни телефон,
    ни код, ни облачный пароль агент не видит и не хранит. Остаётся только
    файл сессии — он в .gitignore и равен доступу к аккаунту, так что
    отдавать его никому нельзя.
    """
    config = _config()
    connector = build_connector("telegram", config)
    try:
        client = connector.client()
    except ConnectorError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    console.print(f"Файл сессии: [green]{connector.session_path}[/green]")
    with client:
        me = client.get_me()
        console.print(f"Вход выполнен: [green]{me.first_name} (@{me.username})[/green]")
    connector.close()


@telegram_app.command("channels")
def telegram_channels() -> None:
    """Проверить, какие каналы настроены и читаются ли они."""
    config = _config()
    connector = build_connector("telegram", config)
    if not connector.channels:
        console.print("[yellow]Каналы не заданы[/yellow]: "
                      "platforms.telegram.channels в config/settings.yaml")
        raise typer.Exit(1)

    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=7)
    for channel in connector.channels:
        try:
            посты = connector._posts(channel, since)
        except Exception as exc:
            console.print(f"  @{channel}: [red]не прочитался[/red] ({type(exc).__name__})")
            continue
        вакансий = sum(
            1 for post in посты if connector._to_vacancy(channel, post) is not None
        )
        console.print(
            f"  @{channel}: постов за неделю {len(посты)}, из них вакансий {вакансий}"
        )
    connector.close()


@app.command()
def search() -> None:
    """Найти вакансии по активному профилю и записать в базу."""
    config = _config()
    outcome = run_search(config)
    console.print(str(outcome))
    if outcome.errors:
        raise typer.Exit(1)


@app.command()
def score(
    again: bool = typer.Option(
        False, "--again", help="Переоценить и уже оценённые: сменился профиль или движок"
    ),
) -> None:
    """Оценить вакансии, которые ещё не оценивались под активный профиль."""
    config = _config()
    try:
        outcome = run_score(config, again=again)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(str(outcome))


@app.command()
def vacancies() -> None:
    """Собрать отчёт по оценённым вакансиям в output/."""
    config = _config()
    md_path, csv_path = write_report(config)
    console.print(f"Отчёт: [green]{md_path}[/green]")
    console.print(f"Таблица: [green]{csv_path}[/green]")


@app.command()
def prune(
    days: int = typer.Option(14, help="Сколько суток вакансии не быть в выдаче"),
) -> None:
    """Удалить вакансии, которых давно нет в выдаче (п. 3.11 Условий hh.ru)."""
    config = _config()
    console.print(str(run_prune(config, days)))


@app.command()
def apply(
    limit: int = typer.Option(None, help="Не больше этого числа за запуск"),
) -> None:
    """Подготовить письма, проверить факты и (в approve/auto) отправить отклики."""
    config = _config()
    try:
        outcome = run_apply(config, limit)
    except (ConfigError, ResumeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"Режим: [green]{config.settings.mode.value}[/green]")
    console.print(str(outcome))
    if outcome.blocked:
        raise typer.Exit(1)


@app.command()
def mode(
    value: str = typer.Argument(None, help="dry_run | approve | auto"),
    yes: bool = typer.Option(False, "--yes", help="Подтвердить включение auto"),
) -> None:
    """Показать или сменить режим работы."""
    config = _config()
    if value is None:
        console.print(f"Режим: [green]{config.settings.mode.value}[/green]")
        return
    try:
        new_mode = Mode(value)
    except ValueError as exc:
        console.print(f"[red]Неизвестный режим {value!r}: dry_run, approve или auto[/red]")
        raise typer.Exit(2) from exc

    if new_mode is Mode.auto and not yes:
        # ТЗ: auto включается отдельной командой после подтверждения
        console.print(
            "[yellow]Режим auto отправляет отклики сам[/yellow], без показа каждого: "
            f"до {config.settings.limits.applications_per_day} в сутки, "
            f"с оценкой не ниже {config.threshold}.\n"
            "Отклик необратим: работодатель увидит его сразу.\n"
            "Если согласен — повтори команду с [green]--yes[/green]."
        )
        raise typer.Exit(1)

    set_mode(new_mode, config.root)
    console.print(f"Режим: [green]{new_mode.value}[/green]")


@app.command()
def report() -> None:
    """Рыночный отчёт: зарплаты, частые требования, динамика, что подтянуть."""
    config = _config()
    try:
        path = write_market_report(config)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(f"Отчёт: [green]{path}[/green]")


@app.command()
def digest() -> None:
    """Недельный дайджест: что произошло и что требует решения."""
    config = _config()
    console.print(f"Дайджест: [green]{write_digest(config)}[/green]")


@app.command()
def interview(vacancy_id: int) -> None:
    """Собрать досье к собеседованию по вакансии из базы."""
    config = _config()
    try:
        master = load_master_profile(config.root)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    conn = open_db(config.db_path)
    try:
        row = conn.execute(
            "SELECT v.*, (SELECT score FROM scores WHERE vacancy_id = v.id "
            " AND profile = ? ORDER BY created_at DESC LIMIT 1) AS score "
            "FROM vacancies v WHERE v.id = ?",
            (config.profile.name, vacancy_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        console.print(f"[red]Вакансии {vacancy_id} нет в базе.[/red]")
        raise typer.Exit(1)

    dossier = prepare(vacancy_from_row(row), master, score=row["score"])
    path = write_dossier(config, dossier, vacancy_id)
    console.print(f"Досье: [green]{path}[/green]")


if __name__ == "__main__":
    app()
