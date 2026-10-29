"""Загрузка и валидация конфигурации.

Ни одного лимита, порога или имени модели в коде быть не должно — всё здесь,
из `config/settings.yaml` и `config/search_profiles/<name>.yaml`.
"""

from __future__ import annotations

import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ConfigError(RuntimeError):
    """Конфигурация непригодна: нет файла, не прошла валидацию, нет секрета."""


class Mode(str, Enum):
    dry_run = "dry_run"
    approve = "approve"
    auto = "auto"


class Access(str, Enum):
    api = "api"
    rss = "rss"
    manual = "manual"


class ApplyMode(str, Enum):
    api = "api"
    draft = "draft"


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applications_per_day: int = Field(10, ge=0)
    score_threshold: int = Field(70, ge=0, le=100)
    request_delay_seconds: float = Field(1.5, ge=0)
    max_requests_per_minute: int = Field(30, ge=1)


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model: str
    model_heavy: str | None = None
    max_tokens: int = Field(4096, ge=256)


class PlatformSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    access: Access = Access.manual
    apply: ApplyMode = ApplyMode.draft
    # Можно ли отправлять текст вакансии этой площадки в модель. У hh.ru
    # п. 4.6 Условий запрещает передавать полученные данные сторонним
    # сервисам, а модель — сторонний сервис (docs/hh-application.md)
    use_model: bool = False
    # Только для telegram: публичные каналы, которые читаем
    channels: list[str] = []


class Paths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    db: Path = Path("output/jobagent.sqlite3")
    output_dir: Path = Path("output")


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search: str | None = None
    market_report: str | None = None


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = Mode.dry_run
    active_profile: str
    limits: Limits = Limits()
    llm: LLMSettings
    platforms: dict[str, PlatformSettings] = {}
    paths: Paths = Paths()
    schedule: Schedule = Schedule()

    def platform(self, name: str) -> PlatformSettings:
        try:
            return self.platforms[name]
        except KeyError as exc:
            raise ConfigError(
                f"площадка {name!r} не описана в config/settings.yaml"
            ) from exc


class SearchProfile(BaseModel):
    """Вся специфика сферы поиска. Смена сферы = другой файл, не правка кода."""

    model_config = ConfigDict(extra="forbid")

    name: str
    title: str | None = None
    keywords: list[str] = []
    exclude_keywords: list[str] = []
    regions: list[str] = []
    schedule: list[str] = []
    employment: list[str] = []
    salary_min: int | None = None
    level: str | None = None
    must_have_skills: list[str] = []
    nice_to_have_skills: list[str] = []
    deal_breakers: list[str] = []
    score_threshold: int | None = Field(None, ge=0, le=100)

    @field_validator("keywords")
    @classmethod
    def _keywords_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("в профиле поиска нужен хотя бы один keyword")
        return v


class Config(BaseModel):
    """Собранная конфигурация: настройки + активный профиль поиска."""

    model_config = ConfigDict(extra="forbid")

    root: Path
    settings: Settings
    profile: SearchProfile

    @property
    def db_path(self) -> Path:
        return self._resolve(self.settings.paths.db)

    @property
    def output_dir(self) -> Path:
        return self._resolve(self.settings.paths.output_dir)

    @property
    def threshold(self) -> int:
        """Порог профиля важнее общего: сфера сфере не равна."""
        if self.profile.score_threshold is not None:
            return self.profile.score_threshold
        return self.settings.limits.score_threshold

    def _resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else self.root / p


def project_root() -> Path:
    """Корень репозитория: src/jobagent/config.py -> ../../.."""
    return Path(__file__).resolve().parents[2]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"нет файла конфигурации: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: ожидался словарь на верхнем уровне")
    return data


def load_settings(root: Path | None = None) -> Settings:
    root = root or project_root()
    return Settings.model_validate(_read_yaml(root / "config" / "settings.yaml"))


def load_profile(name: str, root: Path | None = None) -> SearchProfile:
    root = root or project_root()
    path = root / "config" / "search_profiles" / f"{name}.yaml"
    data = _read_yaml(path)
    data.setdefault("name", name)
    return SearchProfile.model_validate(data)


def load_config(root: Path | None = None, profile: str | None = None) -> Config:
    root = root or project_root()
    settings = load_settings(root)
    return Config(
        root=root,
        settings=settings,
        profile=load_profile(profile or settings.active_profile, root),
    )


def available_profiles(root: Path | None = None) -> list[str]:
    root = root or project_root()
    folder = root / "config" / "search_profiles"
    return sorted(p.stem for p in folder.glob("*.yaml"))


def set_active_profile(name: str, root: Path | None = None) -> None:
    """Меняет active_profile в settings.yaml, не затрагивая остальной файл."""
    root = root or project_root()
    if name not in available_profiles(root):
        raise ConfigError(
            f"профиля {name!r} нет; есть: {', '.join(available_profiles(root)) or '—'}"
        )
    path = root / "config" / "settings.yaml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("active_profile:"):
            lines[i] = f"active_profile: {name}\n"
            break
    else:
        raise ConfigError("в settings.yaml не нашлась строка active_profile")
    path.write_text("".join(lines), encoding="utf-8", newline="\n")


PROFILE_TEMPLATE = """# Профиль поиска «{name}». Вся специфика сферы живёт здесь,
# а не в коде: чтобы искать в другой сфере, достаточно другого такого файла.
# Описание полей — docs/search-profiles.md

name: {name}
title: {title}

keywords:
{keywords}

# отсеивается по названию до обращения к модели
exclude_keywords: []

regions: []          # например: ["Москва", "удалённо"]
schedule: []         # remote / hybrid / office
employment: []       # полная / частичная / проектная
salary_min: null
level: null

must_have_skills: []     # без этого вакансия не подходит
nice_to_have_skills: []  # приятный плюс

# стоп-факторы: вакансия отбрасывается независимо от оценки
deal_breakers: []

score_threshold: {threshold}
"""


def create_profile(
    name: str,
    keywords: list[str],
    *,
    title: str | None = None,
    threshold: int = 70,
    root: Path | None = None,
) -> Path:
    """Создаёт файл профиля поиска под новую сферу. Код при этом не меняется."""
    root = root or project_root()
    if not name or not re.fullmatch(r"[a-z0-9_]+", name):
        raise ConfigError(
            f"имя профиля {name!r} не годится: только строчные латинские буквы, "
            "цифры и подчёркивание — это имя файла"
        )
    keywords = [k.strip() for k in keywords if k.strip()]
    if not keywords:
        raise ConfigError("нужно хотя бы одно ключевое слово для поиска")

    path = root / "config" / "search_profiles" / f"{name}.yaml"
    if path.exists():
        raise ConfigError(f"профиль уже есть: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        PROFILE_TEMPLATE.format(
            name=name,
            # json.dumps даёт корректную YAML-строку в кавычках; safe_dump
            # для отдельного скаляра добавляет маркер конца документа «...»
            title=json.dumps(title or name, ensure_ascii=False),
            keywords="\n".join(
                f"  - {json.dumps(k, ensure_ascii=False)}" for k in keywords
            ),
            threshold=threshold,
        ),
        encoding="utf-8",
        newline="\n",
    )
    # сразу проверяем, что получившийся файл проходит валидацию
    load_profile(name, root)
    return path


def set_mode(mode: Mode, root: Path | None = None) -> None:
    """Меняет режим в settings.yaml, не затрагивая остальной файл."""
    root = root or project_root()
    path = root / "config" / "settings.yaml"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("mode:"):
            lines[i] = f"mode: {mode.value}\n"
            break
    else:
        raise ConfigError("в settings.yaml не нашлась строка mode")
    path.write_text("".join(lines), encoding="utf-8", newline="\n")


_dotenv_loaded = False


def _load_dotenv(root: Path | None = None) -> None:
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv не обязателен, если переменные в окружении
        _dotenv_loaded = True
        return
    load_dotenv((root or project_root()) / ".env")
    _dotenv_loaded = True


def env(name: str, root: Path | None = None) -> str | None:
    """Секрет из .env или окружения. Значение никогда не логируется."""
    _load_dotenv(root)
    value = os.environ.get(name)
    return value.strip() or None if value else None


def require_env(name: str, root: Path | None = None) -> str:
    value = env(name, root)
    if not value:
        raise ConfigError(f"не задана переменная {name} (см. .env.example)")
    return value


def user_agent(root: Path | None = None) -> str:
    """Площадки требуют осмысленный User-Agent с контактом."""
    contact = env("CONTACT_EMAIL", root) or "no-contact-set"
    return f"job-agent/0.1 ({contact})"
