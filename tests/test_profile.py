from __future__ import annotations

import pytest

from jobagent.profile import (
    MasterProfile,
    blocking_gaps,
    find_gaps,
    load_master_profile,
    save_master_profile,
)


def test_пустой_профиль_в_репозитории_честно_пустой(repo):
    """Шаблон не должен содержать выдуманных фактов обо мне."""
    master = load_master_profile(repo)
    assert master.person.full_name is None
    assert master.projects == []
    assert master.employment_history == [], "коммерческого опыта нет и не придумываем"
    assert blocking_gaps(master), "пустой профиль обязан требовать вопросов"


def test_пробелы_делятся_на_обязательные_и_желательные(sample_profile):
    gaps = find_gaps(sample_profile)
    assert not [g for g in gaps if g.blocking], "заполненный профиль ничего не блокирует"


def test_не_хватает_имени_это_блокирующий_пробел(sample_profile):
    broken = sample_profile.model_copy(
        update={"person": sample_profile.person.model_copy(update={"full_name": None})}
    )
    paths = {gap.path for gap in blocking_gaps(broken)}
    assert "person.full_name" in paths


def test_профиль_без_навыков_резюме_не_даёт(sample_profile):
    from jobagent.profile import Skills

    profile = sample_profile.model_copy(update={"skills": Skills()})
    assert "skills" in {gap.path for gap in blocking_gaps(profile)}


def test_проект_без_роли_в_резюме_не_идёт(sample_profile):
    project = sample_profile.projects[0].model_copy(update={"my_role": None})
    profile = sample_profile.model_copy(update={"projects": [project]})
    assert profile.resume_projects == []
    assert any(g.path.startswith("projects[") for g in find_gaps(profile))


def test_проект_без_ссылки_это_замечание_а_не_запрет(sample_profile):
    project = sample_profile.projects[0].model_copy(
        update={"repo_url": None, "demo_url": None, "docs_url": None}
    )
    profile = sample_profile.model_copy(update={"projects": [project]})
    gaps = [g for g in find_gaps(profile) if "repo_url" in g.path]
    assert gaps and not gaps[0].blocking


def test_проекты_подхватываются_из_отдельных_файлов(repo):
    (repo / "profile" / "projects" / "labbot.yaml").write_text(
        "slug: labbot\nname: LabPhotoBot\none_liner: поиск фото наклеек\n"
        "my_role: всё сам\nstack: [Python, aiogram]\n",
        encoding="utf-8",
    )
    master = load_master_profile(repo)
    assert [p.slug for p in master.projects] == ["labbot"]
    assert master.resume_projects[0].name == "LabPhotoBot"


def test_шаблон_проекта_не_попадает_в_профиль(repo):
    """_template.yaml лежит рядом, но это заготовка, а не мой проект."""
    assert (repo / "profile" / "projects" / "_template.yaml").exists()
    assert load_master_profile(repo).projects == []


def test_запись_и_чтение_профиля_сходятся(repo, sample_profile):
    save_master_profile(sample_profile, repo)
    assert load_master_profile(repo) == sample_profile


def test_лишнее_поле_в_профиле_это_ошибка():
    with pytest.raises(Exception):
        MasterProfile.model_validate({"опыт_работы_в_гугле": "10 лет"})
