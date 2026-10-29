from __future__ import annotations

import pytest

from jobagent.config import (
    ConfigError,
    Mode,
    available_profiles,
    load_config,
    load_profile,
    set_active_profile,
)


def test_настройки_читаются(config):
    assert config.settings.mode is Mode.dry_run, "dry_run должен быть по умолчанию"
    assert config.settings.limits.applications_per_day > 0
    assert config.settings.llm.model


def test_профиль_вибкодинга_валиден(config):
    assert config.profile.name == "vibe_coding"
    assert config.profile.keywords


def test_порог_профиля_важнее_общего(repo):
    config = load_config(repo)
    assert config.threshold == config.profile.score_threshold


def test_смена_профиля_не_требует_правок_кода(repo):
    (repo / "config" / "search_profiles" / "cooking.yaml").write_text(
        "name: cooking\nkeywords: [\"повар\"]\nscore_threshold: 55\n",
        encoding="utf-8",
    )
    assert "cooking" in available_profiles(repo)
    set_active_profile("cooking", repo)
    config = load_config(repo)
    assert config.profile.name == "cooking"
    assert config.threshold == 55


def test_неизвестный_профиль_это_понятная_ошибка(repo):
    with pytest.raises(ConfigError):
        set_active_profile("нет-такого", repo)
    with pytest.raises(ConfigError):
        load_profile("нет-такого", repo)


def test_профиль_без_ключевых_слов_не_проходит(repo):
    (repo / "config" / "search_profiles" / "broken.yaml").write_text(
        "name: broken\nkeywords: []\n", encoding="utf-8"
    )
    with pytest.raises(Exception):
        load_profile("broken", repo)
