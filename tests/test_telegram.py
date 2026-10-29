"""Коннектор к телеграм-каналам: разбор постов и сборка вакансий.

Сети здесь нет: `_posts` подменяется списком заготовленных сообщений.
Проверяется то, где живут настоящие ошибки — разбор текста, который
каждый канал пишет как хочет.
"""

from __future__ import annotations

import pytest

from jobagent.config import Access, ApplyMode
from jobagent.connectors import ConnectorError, build_connector
from jobagent.connectors.telegram import TelegramConnector
from jobagent.connectors.telegram_posts import заголовок, зарплата, разобрать

ВАКАНСИЯ = """🔥 Python-разработчик (удалённо)

Компания: ООО Пример
Зарплата: от 150 000 до 220 000 ₽

Требования:
— Python, Docker, PostgreSQL
— опыт с LLM

Обязанности: автоматизация внутренних процессов.
Резюме присылайте @hr_primer
https://example.com/vacancy/1
#вакансия #python
"""


def включённый(config, **overrides):
    """Площадка telegram, включённая для теста."""
    platforms = dict(config.settings.platforms)
    platforms["telegram"] = platforms["telegram"].model_copy(
        update={"enabled": True, "access": Access.api, **overrides}
    )
    return config.model_copy(
        update={"settings": config.settings.model_copy(update={"platforms": platforms})}
    )


class ЗаглушкаКанала(TelegramConnector):
    """Коннектор с заранее известными постами вместо Telegram."""

    посты: dict[str, list[dict]] = {}

    def _posts(self, channel: str, since) -> list[dict]:
        return self.посты.get(channel, [])


def коннектор(config, посты: dict[str, list[dict]], channels=("devjobs",)):
    cfg = включённый(config, channels=list(channels))
    c = ЗаглушкаКанала(cfg)
    c.посты = посты
    return c


# --- разбор текста --------------------------------------------------------


def test_обычная_вакансия_разбирается():
    разбор = разобрать(ВАКАНСИЯ)
    assert разбор.is_vacancy
    assert разбор.title == "Python-разработчик (удалённо)"
    assert (разбор.salary_from, разбор.salary_to) == (150000, 220000)
    assert разбор.currency == "RUB"
    assert разбор.contacts == ["@hr_primer"]
    assert разбор.links == ["https://example.com/vacancy/1"]


def test_заголовок_чистится_от_эмодзи_и_слова_вакансия():
    assert заголовок("🔥🔥 Вакансия: Go-разработчик") == "Go-разработчик"
    assert заголовок("#вакансия\n\nDevOps-инженер") == "DevOps-инженер"
    assert заголовок("—  Senior Python Developer  —") == "Senior Python Developer"


@pytest.mark.parametrize(
    "текст,ожидание",
    [
        ("Зарплата от 150 000 ₽", (150000, None, "RUB")),
        ("150 000 - 220 000 руб", (150000, 220000, "RUB")),
        ("Вилка 150–200к", (150000, 200000, "RUB")),
        ("от 3000 $ на руки", (3000, None, "USD")),
        ("Зарплата по результатам собеседования", (None, None, None)),
    ],
)
def test_зарплата_вытаскивается_из_текста(текст, ожидание):
    assert зарплата(текст) == ожидание


def test_диапазон_в_тысячах_не_путается_с_рублями():
    """«150–200к» — это тысячи, а не сто пятьдесят рублей."""
    low, high, _ = зарплата("Доход 150–200к на руки")
    assert (low, high) == (150000, 200000)


@pytest.mark.parametrize(
    "текст,причина",
    [
        ("", "пустой"),
        ("Дайджест вакансий за неделю: смотрите подборку", "служебный"),
        ("Реклама. Курс со скидкой 50%! erid: 2VSb", "служебный"),
        ("Всем привет, как дела?", "не похоже на вакансию"),
    ],
)
def test_не_вакансии_отсеиваются_с_причиной(текст, причина):
    разбор = разобрать(текст)
    assert not разбор.is_vacancy
    assert причина in разбор.reason


def test_причина_отказа_всегда_написана():
    """Чтобы было видно, почему пост не попал в выдачу."""
    assert разобрать("просто текст").reason


# --- сборка вакансий ------------------------------------------------------


def test_посты_превращаются_в_вакансии(config):
    c = коннектор(
        config,
        {"devjobs": [{"id": 10, "date": "2026-10-29T10:00:00+00:00", "text": ВАКАНСИЯ}]},
    )
    вакансии = list(c.search())

    assert len(вакансии) == 1
    v = вакансии[0]
    assert v.platform == "telegram"
    assert v.external_id == "devjobs/10"
    assert v.url == "https://t.me/devjobs/10"
    assert v.company == "@devjobs"
    assert v.has_text, "весь текст поста остаётся описанием"
    assert v.raw["contacts"] == ["@hr_primer"]


def test_перепечатка_в_другом_канале_не_дублируется(config):
    """Одну вакансию перепечатывают несколько каналов."""
    пост = {"id": 1, "date": None, "text": ВАКАНСИЯ}
    c = коннектор(
        config,
        {"devjobs": [пост], "it_bl": [{**пост, "id": 77}]},
        channels=("devjobs", "it_bl"),
    )
    assert len(list(c.search())) == 1, "второй экземпляр отброшен по отпечатку текста"


def test_служебные_посты_в_вакансии_не_попадают(config):
    c = коннектор(
        config,
        {
            "devjobs": [
                {"id": 1, "date": None, "text": ВАКАНСИЯ},
                {"id": 2, "date": None, "text": "Дайджест вакансий за неделю"},
                {"id": 3, "date": None, "text": "Всем привет!"},
            ]
        },
    )
    assert [v.external_id for v in c.search()] == ["devjobs/1"]


def test_без_каналов_понятная_ошибка(config):
    c = коннектор(config, {}, channels=())
    with pytest.raises(ConnectorError) as exc:
        list(c.search())
    assert "не заданы каналы" in str(exc.value)


def test_в_режиме_manual_ничего_не_читается(config):
    cfg = включённый(config, access=Access.manual, channels=["devjobs"])
    c = ЗаглушкаКанала(cfg)
    c.посты = {"devjobs": [{"id": 1, "date": None, "text": ВАКАНСИЯ}]}
    assert list(c.search()) == []


# --- отклики --------------------------------------------------------------


def test_отклик_в_канале_невозможен_и_это_черновик(config):
    """В каналах отвечают письмом или в личку, поэтому только черновик."""
    cfg = включённый(config, channels=["devjobs"])
    assert cfg.settings.platform("telegram").apply is ApplyMode.draft

    c = ЗаглушкаКанала(cfg)
    from jobagent.connectors import Vacancy

    результат = c.apply(
        Vacancy(platform="telegram", external_id="devjobs/1", title="Разработчик"),
        resume_id="1",
        message="письмо",
    )
    assert (результат.sent, результат.status) == (False, "draft")


def test_коннектор_зарегистрирован(config):
    assert isinstance(build_connector("telegram", config), TelegramConnector)


def test_без_ключей_telegram_говорит_что_делать(config, monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_API_HASH", raising=False)
    c = build_connector("telegram", включённый(config, channels=["devjobs"]))
    with pytest.raises(ConnectorError) as exc:
        c.client()
    assert "my.telegram.org" in str(exc.value)
    assert "telegram login" in str(exc.value)
