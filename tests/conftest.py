from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from jobagent.config import Config, Mode, load_config, project_root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Копия конфигурации репозитория в tmp — тесты не трогают рабочие файлы."""
    root = project_root()
    for folder in ("config", "profile"):
        shutil.copytree(root / folder, tmp_path / folder)
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
def auto_config(config: Config) -> Config:
    """Та же конфигурация, но в режиме auto — для проверок отправки."""
    return config.model_copy(
        update={"settings": config.settings.model_copy(update={"mode": Mode.auto})}
    )
