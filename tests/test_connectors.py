from __future__ import annotations

import httpx
import respx

from jobagent.config import Access
from jobagent.connectors import build_connector, enabled_connectors

RSS = """<?xml version='1.0' encoding='utf-8'?>
<rss version="2.0"><channel>
  <item>
    <pubDate>2026-10-29T08:52:14.814+03:00</pubDate>
    <title>AI-разработчик (Claude Code)</title>
    <link>https://hh.ru/vacancy/138952520</link>
  </item>
  <item>
    <title>Без идентификатора</title>
    <link>https://hh.ru/employer/1234</link>
  </item>
</channel></rss>
"""


def _hh_access(config, access: Access):
    """hh.ru по умолчанию в manual (docs/hh-application.md) — режим задаём явно."""
    platforms = dict(config.settings.platforms)
    platforms["hh"] = platforms["hh"].model_copy(update={"access": access})
    return config.model_copy(
        update={"settings": config.settings.model_copy(update={"platforms": platforms})}
    )


@respx.mock
def test_hh_в_режиме_rss_находит_вакансии(config):
    respx.get("https://hh.ru/search/vacancy/rss").mock(
        return_value=httpx.Response(200, text=RSS)
    )
    connector = build_connector("hh", _hh_access(config, Access.rss))
    assert connector.access is Access.rss

    vacancies = list(connector.search())

    assert len(vacancies) == len(config.profile.keywords)
    first = vacancies[0]
    assert first.external_id == "138952520"
    assert first.title.startswith("AI-разработчик")
    # у RSS нет описания — значит, честно оценивать нечего
    assert first.has_text is False


@respx.mock
def test_superjob_разбирает_выдачу(config, monkeypatch):
    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=httpx.Response(
            200,
            json={
                "objects": [
                    {
                        "id": 26747390,
                        "profession": "Python-разработчик с ИИ",
                        "link": "https://superjob.ru/vakansii/1.html",
                        "client": {"title": "ООО Пример"},
                        "town": {"title": "Москва"},
                        "payment_from": 120000,
                        "payment_to": 0,
                        "currency": "rub",
                        "candidat": "Требуется опыт с Claude Code",
                    }
                ],
                "more": False,
            },
        )
    )
    vacancies = list(build_connector("superjob", config).search())
    assert len(vacancies) == len(config.profile.keywords)
    vacancy = vacancies[0]
    assert vacancy.company == "ООО Пример"
    assert vacancy.salary_from == 120000
    assert vacancy.salary_to is None, "0 означает «не указано», а не ноль рублей"
    assert vacancy.has_text


def test_hh_в_режиме_manual_ничего_не_ищет(config):
    """Режим по умолчанию: ни одного запроса к hh.ru без одобренного приложения."""
    with respx.mock:
        route = respx.route().mock(side_effect=AssertionError("запрос к hh в manual"))
        assert list(build_connector("hh", config).search()) == []
        assert route.call_count == 0


@respx.mock
def test_разбор_выдачи_superjob_по_живому_ответу(config, monkeypatch):
    """Поля и их форма взяты из настоящего ответа (tools/probe_superjob.py)."""
    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=httpx.Response(
            200,
            json={
                "objects": [
                    {
                        "id": 52150505,
                        "profession": "Специалист технической поддержки",
                        "link": "https://www.superjob.ru/vakansii/x-52150505.html",
                        "client": {"id": 4970534, "title": "Flowwow"},
                        "town": {"id": 4, "title": "Москва"},
                        "payment_from": 0,
                        "payment_to": 0,
                        "currency": "rub",
                        "type_of_work": {"id": 6, "title": "Полный рабочий день"},
                        "candidat": "Требования\n* Python и Docker",
                        "vacancyRichText": "<b>Требования</b><br />* Python &amp; Docker",
                        "date_published": 1792856488,
                    }
                ],
                "more": False,
            },
        )
    )
    vacancy = next(iter(build_connector("superjob", config).search()))

    assert vacancy.salary_from is None and vacancy.salary_to is None, (
        "0 в вилке означает «не указано»"
    )
    assert vacancy.description == "Требования\n* Python и Docker", (
        "берём candidat: в vacancyRichText приходят теги"
    )
    assert "<b>" not in (vacancy.description or "")
    assert vacancy.published_at.startswith("2026-"), (
        "date_published приходит временной меткой Unix, а не строкой"
    )


def test_разметка_вычищается_если_простого_текста_нет(config, monkeypatch):
    from jobagent.connectors.superjob import SuperJobConnector

    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    vacancy = SuperJobConnector(config)._from_api(
        {
            "id": 1,
            "profession": "Разработчик",
            "vacancyRichText": "<b>Требования</b><br />Python &amp; Docker",
        }
    )
    assert "<" not in vacancy.description
    assert "&" in vacancy.description, "мнемоники HTML раскрыты: &amp; -> &"


@respx.mock
def test_мнемоники_html_в_названии_раскрываются(config, monkeypatch):
    from jobagent.connectors.superjob import SuperJobConnector

    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    vacancy = SuperJobConnector(config)._from_api(
        {"id": 1, "profession": "Mod Developer (Python &amp; React)"}
    )
    assert vacancy.title == "Mod Developer (Python & React)"


@respx.mock
def test_города_профиля_превращаются_в_фильтр(config, monkeypatch):
    """Без этого выдача приходит по всей стране, хотя переезда нет."""
    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    respx.get("https://api.superjob.ru/2.0/towns/").mock(
        return_value=httpx.Response(
            200, json={"objects": [{"id": 4, "title": "Москва"}]}
        )
    )
    выдача = respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=httpx.Response(200, json={"objects": [], "more": False})
    )
    профиль = config.profile.model_copy(update={"regions": ["Москва", "удалённо"]})
    list(build_connector("superjob", config.model_copy(update={"profile": профиль})).search())

    assert выдача.called
    параметры = httpx.QueryParams(выдача.calls[0].request.url.query)
    assert параметры.get_list("t") == ["4"], (
        "город передан фильтром площадки, а «удалённо» городом не считается"
    )


@respx.mock
def test_закрытые_вакансии_в_базу_не_идут(config, monkeypatch):
    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    respx.get("https://api.superjob.ru/2.0/vacancies/").mock(
        return_value=httpx.Response(
            200,
            json={
                "objects": [
                    {"id": 1, "profession": "Живая", "candidat": "текст"},
                    {"id": 2, "profession": "Закрытая", "candidat": "текст",
                     "is_closed": True},
                    {"id": 3, "profession": "В архиве", "candidat": "текст",
                     "is_archive": True},
                ],
                "more": False,
            },
        )
    )
    найдено = {v.external_id for v in build_connector("superjob", config).search()}
    assert найдено == {"1"}


def test_включены_только_настроенные_площадки(config):
    names = {c.name for c in enabled_connectors(config)}
    assert names == {"hh", "superjob"}
    assert "rabota" not in names, "выключенная площадка не должна попадать в работу"
