from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from jobagent.config import Config, Mode, load_config, project_root
from jobagent.profile import MasterProfile


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Копия конфигурации репозитория в tmp — тесты не трогают рабочие файлы.

    Настоящие файлы проектов нарочно не копируются: тесты механики не должны
    зависеть от того, сколько проектов у пользователя сегодня. Реальный профиль
    проверяется отдельно, в test_real_profile.py.
    """
    root = project_root()
    shutil.copytree(root / "config", tmp_path / "config")
    (tmp_path / "profile" / "projects").mkdir(parents=True)
    # из примера, а не из моего профиля: в чистой копии репозитория
    # master_profile.yaml ещё не создан, он в .gitignore
    shutil.copy(
        root / "profile" / "master_profile.example.yaml",
        tmp_path / "profile" / "master_profile.yaml",
    )
    shutil.copy(
        root / "profile" / "projects" / "_template.yaml",
        tmp_path / "profile" / "projects" / "_template.yaml",
    )
    return tmp_path


@pytest.fixture
def config(repo: Path) -> Config:
    """Рабочая конфигурация, но без пауз между запросами — тесты не ждут сеть."""
    config = load_config(repo)
    limits = config.settings.limits.model_copy(update={"request_delay_seconds": 0.0})
    return config.model_copy(
        update={"settings": config.settings.model_copy(update={"limits": limits})}
    )


@pytest.fixture
def sample_profile() -> MasterProfile:
    """Вымышленный, но полный профиль. Нужен, чтобы проверять механику сборки
    резюме, не подставляя в тесты реальные факты пользователя."""
    return MasterProfile.model_validate(
        {
            "person": {
                "full_name": "Иван Петров",
                "city": "Москва",
                "email": "ivan@example.com",
                "telegram": "@ivan",
                "github": "https://github.com/ivan",
            },
            "position": {
                "target_titles": ["Python-разработчик"],
                "level": "junior",
                "salary_expectation": "от 120 000 ₽",
                "employment": ["полная"],
                "schedule": ["удалённо"],
            },
            "summary": "Собираю рабочие инструменты на Python с помощью ИИ-агентов.",
            "projects": [
                {
                    "slug": "notebot",
                    "name": "NoteBot",
                    "one_liner": "телеграм-бот для заметок",
                    "my_role": "придумал, написал и развернул сам",
                    "stack": ["Python", "aiogram", "SQLite"],
                    "result": "пользуются двенадцать человек",
                    "repo_url": "https://github.com/ivan/notebot",
                    "in_production": True,
                }
            ],
            "skills": {
                "languages": ["Python"],
                "tools": ["Claude Code", "git", "Docker"],
                "databases": ["SQLite", "PostgreSQL"],
            },
            "languages": [{"name": "русский", "level": "родной"}],
            "education": [
                {
                    "institution": "МГТУ",
                    "speciality": "информатика",
                    "years": "2015–2019",
                }
            ],
        }
    )


@pytest.fixture
def auto_config(config: Config) -> Config:
    """Та же конфигурация, но в режиме auto — для проверок отправки."""
    return config.model_copy(
        update={"settings": config.settings.model_copy(update={"mode": Mode.auto})}
    )
