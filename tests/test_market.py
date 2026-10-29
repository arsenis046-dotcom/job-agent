from __future__ import annotations

import pytest

from jobagent.agents.interview import prepare
from jobagent.agents.market import analyze, save_snapshot
from jobagent.agents.portfolio import advise
from jobagent.connectors import Vacancy
from jobagent.db import open_db, upsert_vacancy
from jobagent.profile import save_master_profile
from jobagent.reports.market import write_digest, write_dossier, write_market_report


@pytest.fixture
def рынок(repo, config, sample_profile):
    """Несколько вакансий в базе: есть и вилки, и повторяющиеся термины."""
    save_master_profile(sample_profile, repo)
    данные = [
        ("Python-разработчик", "Python, Docker, Kubernetes", 100000, 150000),
        ("AI-разработчик", "Python, LLM, Docker", 120000, None),
        ("Промпт-инженер", "LLM, Kubernetes, Airflow", None, None),
        ("Разработчик с ИИ", "Python, SQLite, Claude Code", 90000, 110000),
    ]
    conn = open_db(config.db_path)
    try:
        for i, (title, description, low, high) in enumerate(данные):
            upsert_vacancy(
                conn,
                platform="superjob",
                external_id=str(i),
                title=title,
                description=description,
                salary_from=low,
                salary_to=high,
                currency="rub",
            )
    finally:
        conn.close()
    return config


def отчёт(config, master):
    conn = open_db(config.db_path)
    try:
        return analyze(conn, config.profile.name, master)
    finally:
        conn.close()


def test_термин_считается_по_числу_вакансий_а_не_упоминаний(рынок, sample_profile):
    conn = open_db(рынок.db_path)
    try:
        upsert_vacancy(
            conn,
            platform="superjob",
            external_id="повтор",
            title="Python Python Python",
            description="Python Python Python Python",
        )
    finally:
        conn.close()

    report = отчёт(рынок, sample_profile)
    assert report.current.terms_dict["python"] == 4, (
        "вакансия с двадцатью упоминаниями весит как одна"
    )


def test_стек_проектов_считается_владением(sample_profile):
    """У человека с четырьмя сервисами «закрыто 0%» быть не может."""
    from jobagent.profile import Skills

    без_навыков = sample_profile.model_copy(update={"skills": Skills()})
    assert без_навыков.skills.total == 0
    умею = {s.casefold() for s in без_навыков.known_skills}
    assert {"python", "aiogram", "sqlite"} <= умею, "стек проекта — тоже факт профиля"


def test_термин_находится_внутри_составного_названия(repo, config, sample_profile):
    """«docker» в вакансии закрыт навыком «Docker Compose»."""
    from jobagent.profile import Skills

    профиль = sample_profile.model_copy(
        update={"skills": Skills(tools=["Docker Compose"]), "projects": []}
    )
    conn = open_db(config.db_path)
    try:
        upsert_vacancy(
            conn,
            platform="superjob",
            external_id="1",
            title="Разработчик",
            description="Нужен Docker и Kubernetes, плюс github",
        )
    finally:
        conn.close()

    report = отчёт(config, профиль)
    assert "docker" in {t for t, _ in report.covered}
    assert "kubernetes" in {t for t, _ in report.uncovered}
    assert "github" in {t for t, _ in report.uncovered}, "«git» внутри «github» не считается"


def test_обрывки_с_дефисом_в_термины_не_попадают(repo, config, sample_profile):
    """«AI-разработчик» токенизатор режет на «AI-», и дефис остаётся висеть."""
    conn = open_db(config.db_path)
    try:
        upsert_vacancy(
            conn,
            platform="superjob",
            external_id="1",
            title="AI-разработчик",
            description="Python-разработчик нужен",
        )
    finally:
        conn.close()
    terms = отчёт(config, sample_profile).current.terms_dict
    assert "ai-" not in terms and "python-" not in terms
    assert "ai" not in terms, "двухбуквенный шум отфильтрован списком"
    assert terms["python"] == 1


def test_число_вакансий_склоняется():
    from jobagent.reports.market import вакансий

    assert вакансий(1) == "1 вакансии"
    assert вакансий(2) == "2 вакансиях"
    assert вакансий(11) == "11 вакансиях", "одиннадцать — не «вакансии»"
    assert вакансий(21) == "21 вакансии"


def test_зарплатные_квартили_считаются_по_известным_вилкам(рынок, sample_profile):
    snapshot = отчёт(рынок, sample_profile).current
    assert snapshot.vacancies == 4
    assert snapshot.with_salary == 3, "вакансия без вилки в расчёт не идёт"
    assert snapshot.salary_median == 120000


def test_шум_в_термины_не_попадает(рынок, sample_profile):
    terms = отчёт(рынок, sample_profile).current.terms_dict
    for noise in ("and", "for", "senior", "ru", "it"):
        assert noise not in terms


def test_мои_навыки_отделены_от_пробелов(рынок, sample_profile):
    report = отчёт(рынок, sample_profile)
    covered = {t for t, _ in report.covered}
    gaps = {t for t, _ in report.uncovered}
    assert "python" in covered and "docker" in covered
    assert "kubernetes" in gaps and "airflow" in gaps
    assert not covered & gaps, "термин не может быть одновременно и там, и там"


def test_динамика_считается_от_предыдущего_снимка(рынок, sample_profile):
    first = отчёт(рынок, sample_profile)
    conn = open_db(рынок.db_path)
    try:
        save_snapshot(conn, first.current)
        upsert_vacancy(
            conn,
            platform="superjob",
            external_id="новая",
            title="Разработчик",
            description="Kubernetes, Kubernetes нужен везде",
        )
    finally:
        conn.close()

    second = отчёт(рынок, sample_profile)
    assert second.previous is not None
    assert second.vacancies_delta == 1
    assert ("kubernetes", 1) in second.growing()


def test_советы_по_портфолио_опираются_на_частоты(рынок, sample_profile):
    report = отчёт(рынок, sample_profile)
    advice = advise(report, sample_profile)
    assert advice.coverage_percent is not None
    assert [t for t, _ in advice.gaps][:1] == ["kubernetes"], "первым — самый частый пробел"
    сильные = {c.project.slug for c in advice.strongest}
    assert "notebot" in сильные, "проект на Python попадает в частые требования"


def test_рыночный_отчёт_пишется_с_оговоркой(рынок, sample_profile):
    path = write_market_report(рынок)
    text = path.read_text(encoding="utf-8")
    assert "## Зарплаты" in text
    assert "kubernetes" in text
    assert "приближение, а не справочник" in text, "честная оговорка обязательна"


def test_дайджест_показывает_что_требует_решения(рынок):
    text = write_digest(рынок).read_text(encoding="utf-8")
    assert "вакансий в базе | 4" in text
    assert "требуют моего решения" in text


def test_досье_честно_говорит_про_отсутствие_опыта(sample_profile):
    dossier = prepare(
        Vacancy(
            platform="superjob",
            external_id="1",
            title="Python-разработчик",
            description="Нужен Python, Docker и Kubernetes",
            company="ООО Пример",
        ),
        sample_profile,
        score=78,
    )
    assert "Коммерческого опыта по найму у меня нет" in dossier.honest_answer
    assert "NoteBot" in dossier.honest_answer, "вместо оправданий — факты о проектах"
    assert "python" in dossier.matched and "docker" in dossier.matched
    assert "kubernetes" in dossier.to_learn
    assert dossier.projects, "рассказывать есть о чём"


def test_досье_сохраняется_файлом(рынок, sample_profile):
    dossier = prepare(
        Vacancy(
            platform="superjob", external_id="1", title="Python-разработчик",
            description="Python, Kubernetes",
        ),
        sample_profile,
    )
    text = write_dossier(рынок, dossier, 42).read_text(encoding="utf-8")
    assert "## Вопросы работодателю" in text
    assert "- [ ]" in text, "чек-лист сделан отмечаемым"
    assert "Коммерческого опыта" in text
