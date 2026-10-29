"""Разбор поста с вакансией из телеграм-канала.

Самая ненадёжная часть коннектора, поэтому вынесена отдельно и покрыта
тестами: канал — это не API, там нет полей, есть текст, который каждый
пишет как хочет. Задача скромная и честная: понять, вакансия ли это
вообще, вытащить заголовок, зарплату и ссылку для отклика. Всё
непонятное остаётся в описании целиком, пусть читает модель или я.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# «150 000 ₽», «от 150000 руб», «от 150 000 до 220 000 ₽», «$3000», «150–200к».
# Пробелы внутри числа бывают обычные, неразрывные и узкие неразрывные.
ПРОБЕЛЫ = r"\s  "
# Граница — (?!\w), а не \b: после «₽» или «$» словарной границы нет,
# и \b молча не срабатывал. На этом разбор «от 150 000 ₽» и терял зарплату.
ДЕНЬГИ = re.compile(
    rf"(?P<from>\d[\d{ПРОБЕЛЫ}]{{2,}})\s*"
    rf"(?:(?:[–—-]|до)\s*(?P<to>\d[\d{ПРОБЕЛЫ}]{{2,}}))?\s*"
    r"(?P<cur>₽|руб|р\.|rub|\$|usd|€|eur|k|к)(?!\w)",
    re.IGNORECASE,
)
ТЫСЯЧИ = re.compile(
    r"(?P<from>\d{2,3})\s*[–—-]\s*(?P<to>\d{2,3})\s*(?:k|к)(?!\w)", re.IGNORECASE
)
ССЫЛКА = re.compile(r"https?://\S+")
КОНТАКТ = re.compile(r"@[A-Za-z][A-Za-z0-9_]{3,}")
ЭМОДЗИ_И_МУСОР = re.compile(
    r"^[\s\W_]*(?:вакансия|vacancy|ищем|требуется|открыта вакансия)?[\s:—–-]*",
    re.IGNORECASE,
)
ХЕШТЕГ = re.compile(r"#\w+")

# Слова, по которым пост опознаётся как вакансия
ПРИЗНАКИ = (
    "вакансия",
    "ищем",
    "требуется",
    "зарплата",
    "оклад",
    "резюме",
    "обязанности",
    "требования",
    "мы предлагаем",
    "удалённо",
    "удаленно",
    "гибрид",
    "офис",
    "занятость",
    "senior",
    "middle",
    "junior",
    "разработчик",
    "инженер",
)

# Служебные посты канала, которые вакансиями не являются
НЕ_ВАКАНСИЯ = (
    "реклама",
    "дайджест",
    "подборка вакансий",
    "итоги недели",
    "розыгрыш",
    "вебинар",
    "курс со скидкой",
    "erid",
)

ВАЛЮТЫ = {
    "₽": "RUB",
    "руб": "RUB",
    "р.": "RUB",
    "rub": "RUB",
    "к": "RUB",
    "k": "RUB",
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
}


@dataclass
class Пост:
    """Что удалось понять из текста поста."""

    is_vacancy: bool
    title: str = ""
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str | None = None
    contacts: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    reason: str = ""


def _число(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return int(digits) if digits else None


def зарплата(text: str) -> tuple[int | None, int | None, str | None]:
    """Вилка из текста. Диапазон «150–200к» означает тысячи, а не рубли."""
    тысячи = ТЫСЯЧИ.search(text)
    if тысячи:
        low, high = _число(тысячи.group("from")), _число(тысячи.group("to"))
        return (low * 1000 if low else None, high * 1000 if high else None, "RUB")

    match = ДЕНЬГИ.search(text)
    if not match:
        return (None, None, None)
    low, high = _число(match.group("from")), _число(match.group("to"))
    currency = ВАЛЮТЫ.get(match.group("cur").casefold())
    # «от 150к» — одиночное число с суффиксом тысяч
    if match.group("cur").casefold() in ("к", "k"):
        low = low * 1000 if low and low < 1000 else low
        high = high * 1000 if high and high < 1000 else high
    return (low, high, currency)


def заголовок(text: str) -> str:
    """Первая осмысленная строка без эмодзи, хештегов и слова «Вакансия»."""
    for line in text.splitlines():
        чистая = ХЕШТЕГ.sub("", line).strip()
        чистая = ЭМОДЗИ_И_МУСОР.sub("", чистая).strip(" :—–-•*_")
        if len(чистая) >= 3:
            return чистая[:120]
    return ""


def разобрать(text: str) -> Пост:
    """Пост → вакансия или отказ с причиной."""
    if not text or not text.strip():
        return Пост(is_vacancy=False, reason="пустой пост")

    lowered = text.casefold()
    for слово in НЕ_ВАКАНСИЯ:
        if слово in lowered:
            return Пост(is_vacancy=False, reason=f"служебный пост: «{слово}»")

    признаки = [с for с in ПРИЗНАКИ if с in lowered]
    if len(признаки) < 2:
        return Пост(
            is_vacancy=False,
            reason="не похоже на вакансию: мало признаков "
            f"({', '.join(признаки) or 'ни одного'})",
        )

    title = заголовок(text)
    if not title:
        return Пост(is_vacancy=False, reason="не нашёлся заголовок")

    low, high, currency = зарплата(text)
    return Пост(
        is_vacancy=True,
        title=title,
        salary_from=low,
        salary_to=high,
        currency=currency,
        contacts=sorted(set(КОНТАКТ.findall(text))),
        links=sorted(set(ССЫЛКА.findall(text))),
    )
