"""Один запрос к SuperJob: проверить ключ и увидеть настоящую форму ответа.

Запуск: `uv run python tools/probe_superjob.py [ключевое слово]`

Зачем отдельно от поиска: разбор выдачи (`_from_api` в коннекторе) написан
по документации, а не по живому ответу. Пробник делает **один** запрос и
печатает, какие поля реально пришли и что в них лежит, — чтобы несовпадение
обнаружилось сразу, а не после восьми ключевых слов и сотни записей в базе.

Ключ не печатается.
"""

from __future__ import annotations

import json
import sys

import httpx

from jobagent.config import env, load_config, user_agent

API = "https://api.superjob.ru/2.0/vacancies/"
# Поля, на которые опирается коннектор
ОЖИДАЕМЫЕ = (
    "id",
    "profession",
    "link",
    "client",
    "town",
    "payment_from",
    "payment_to",
    "currency",
    "type_of_work",
    "place_of_work",
    "candidat",
    "vacancyRichText",
    "date_published",
)


def main() -> int:
    config = load_config()
    secret = env("SUPERJOB_SECRET", config.root)
    if not secret:
        print("Нет SUPERJOB_SECRET в .env — вписать секретный ключ приложения.")
        return 2
    if not secret.isascii():
        print("SUPERJOB_SECRET содержит нелатинские символы: проверь, не попал ли")
        print("лишний символ при копировании. В заголовок такое не уходит.")
        return 2

    keyword = sys.argv[1] if len(sys.argv) > 1 else config.profile.keywords[0]
    print(f"Запрос: keyword={keyword!r}, count=3\n")

    response = httpx.get(
        API,
        params={"keyword": keyword, "count": 3},
        headers={"X-Api-App-Id": secret, "User-Agent": user_agent(config.root)},
        timeout=30.0,
    )
    print(f"HTTP {response.status_code}")
    if response.status_code != 200:
        print(response.text[:400])
        return 1

    data = response.json()
    print(f"всего найдено: {data.get('total')}, в ответе: {len(data.get('objects', []))}")
    print(f"есть ещё страницы: {data.get('more')}\n")

    objects = data.get("objects") or []
    if not objects:
        print("По этому слову ничего не нашлось — попробуй другое.")
        return 0

    first = objects[0]
    print("--- поля, на которые опирается коннектор ---")
    for field in ОЖИДАЕМЫЕ:
        if field not in first:
            print(f"  {field:18} ОТСУТСТВУЕТ")
            continue
        value = first[field]
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if k in ("title", "id")}
        text = json.dumps(value, ensure_ascii=False)
        print(f"  {field:18} {text[:90]}")

    лишние = sorted(set(first) - set(ОЖИДАЕМЫЕ))
    print(f"\n--- остальные поля ({len(лишние)}) ---")
    print("  " + ", ".join(лишние))

    print("\n--- как это увидит агент ---")
    from jobagent.connectors import build_connector

    connector = build_connector("superjob", config)
    for item in objects:
        vacancy = connector._from_api(item)
        деньги = f"{vacancy.salary_from or '?'}–{vacancy.salary_to or '?'}"
        print(f"  [{vacancy.external_id}] {vacancy.title}")
        print(f"      компания: {vacancy.company} | город: {vacancy.area} | {деньги}")
        print(f"      описание: {'есть' if vacancy.has_text else 'НЕТ'}, "
              f"{len(vacancy.description or '')} символов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
