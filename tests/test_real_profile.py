"""Проверки настоящего профиля, если он есть на этой машине.

Сам профиль в репозиторий не попадает (он в .gitignore): это личные данные.
Поэтому тесты пропускаются там, где профиля нет, — на чистой копии
репозитория и в чужом клоне.

Следят за тем, чтобы в профиль не просочилось то, чего в нём быть не должно:
коммерческий опыт, нерабочие ссылки, проекты без фактов.
"""

from __future__ import annotations

import pytest
import yaml

from jobagent.profile import load_master_profile, profile_path, projects_dir

pytestmark = pytest.mark.skipif(
    not profile_path().exists(),
    reason="мастер-профиль не заполнен: cp profile/master_profile.example.yaml "
    "profile/master_profile.yaml",
)


def test_все_файлы_проектов_читаются():
    for path in projects_dir().glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{path.name}: ожидался словарь"
        assert data.get("slug"), f"{path.name}: нет slug"


def test_коммерческого_опыта_в_профиле_нет():
    """Пет-проекты не маскируются под работу по найму."""
    assert load_master_profile().employment_history == []


def test_slug_не_повторяются():
    slugs = [p.slug for p in load_master_profile().projects]
    assert len(slugs) == len(set(slugs))


def test_у_проектов_для_резюме_есть_все_нужные_факты():
    for project in load_master_profile().resume_projects:
        assert project.name and project.one_liner
        assert project.stack, f"{project.slug}: не указан стек"
        assert project.my_role, f"{project.slug}: не указана моя роль"


def test_ссылки_выглядят_ссылками():
    for project in load_master_profile().projects:
        for url in (project.repo_url, project.demo_url, project.docs_url):
            if url is not None:
                assert url.startswith("https://"), f"{project.slug}: странная ссылка {url}"
