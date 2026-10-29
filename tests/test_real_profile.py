"""Проверки настоящего профиля в репозитории, а не вымышленного.

Следят за тем, чтобы в профиль не просочилось то, чего в нём быть не должно:
коммерческий опыт, нерабочие ссылки, проекты без фактов.
"""

from __future__ import annotations

import yaml

from jobagent.profile import load_master_profile, projects_dir


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


def test_приватных_репозиториев_в_ссылках_нет():
    """Приватная ссылка на GitHub в резюме выглядит как битая.

    Проверенные 29.10.2026 запросом к api.github.com: все три репозитория
    пользователя отдают 404 неавторизованному гостю.
    """
    private = {
        "https://github.com/arsenis046-dotcom/anketa-forms",
        "https://github.com/arsenis046-dotcom/gruzoperevozki",
        "https://github.com/arsenis046-dotcom/docugen",
    }
    for project in load_master_profile().projects:
        assert project.repo_url not in private, (
            f"{project.slug}: ссылка на приватный репозиторий"
        )
