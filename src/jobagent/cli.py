"""CLI. Команды появляются по фазам; нереализованные честно об этом говорят."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import (
    Config,
    ConfigError,
    available_profiles,
    env,
    load_config,
    set_active_profile,
)
from .connectors import REGISTRY
from .db import SCHEMA_VERSION, open_db, sent_today

console = Console()

app = typer.Typer(
    help="Job Agent — личный ассистент по поиску работы.",
    no_args_is_help=True,
    add_completion=False,
)
profile_app = typer.Typer(help="Профили поиска и мастер-профиль.", no_args_is_help=True)
app.add_typer(profile_app, name="profile")


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
def profile_new(name: str) -> None:
    """Собрать профиль под новую сферу в диалоге."""
    _phase(5, f"диалоговое создание профиля {name!r}")


@profile_app.command("build")
def profile_build() -> None:
    """Интервью для заполнения мастер-профиля."""
    _phase(1, "Profile Builder")


@app.command()
def resume(vacancy_id: int | None = None) -> None:
    """Собрать резюме: базовое или под вакансию."""
    _phase(1, "Resume Writer и рендер DOCX/PDF")


@app.command()
def search() -> None:
    """Найти вакансии по активному профилю."""
    _phase(2, "Scout")


@app.command()
def score() -> None:
    """Оценить найденные вакансии."""
    _phase(2, "Matcher")


@app.command()
def apply() -> None:
    """Подготовить и (в режимах approve/auto) отправить отклики."""
    _phase(3, "Letter Writer, Fact Checker, Applier")


@app.command()
def report() -> None:
    """Рыночный отчёт и недельный дайджест."""
    _phase(4, "Market Analyst")


@app.command()
def interview(vacancy_id: int) -> None:
    """Подготовка к собеседованию по приглашению."""
    _phase(4, "Interview Coach")


if __name__ == "__main__":
    app()
