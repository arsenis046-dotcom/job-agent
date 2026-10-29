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


def test_включены_только_настроенные_площадки(config):
    names = {c.name for c in enabled_connectors(config)}
    assert names == {"hh", "superjob"}
    assert "rabota" not in names, "выключенная площадка не должна попадать в работу"
