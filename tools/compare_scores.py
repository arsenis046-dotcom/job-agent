"""Сравнить оценку локального движка с оценкой модели на одних вакансиях.

Запуск: `uv run python tools/compare_scores.py`

Читает только базу — запросов никуда не делает. Чтобы было что сравнивать,
нужны оценки обоими движками:

    uv run jobagent score           # локальная (без ключа Anthropic)
    uv run jobagent score --again   # моделью, когда ключ появился

Прежние оценки не затираются, поэтому в базе остаются обе.
"""

from __future__ import annotations

from jobagent.config import load_config
from jobagent.db import open_db, score_history

ВЕРДИКТ = {"apply": "откликаться", "ask": "спросить", "skip": "пропустить"}


def main() -> int:
    config = load_config()
    conn = open_db(config.db_path)
    try:
        rows = score_history(conn, config.profile.name)
    finally:
        conn.close()

    по_вакансиям: dict[int, dict] = {}
    for row in rows:
        запись = по_вакансиям.setdefault(
            row["id"], {"title": row["title"], "company": row["company"]}
        )
        движок = "local" if row["model"] == "local" else "model"
        запись[движок] = row

    сравнимые = [v for v in по_вакансиям.values() if "local" in v and "model" in v]
    if not сравнимые:
        движки = {r["model"] for r in rows}
        print("Сравнивать нечего: в базе оценки только одним движком.")
        print(f"Найденные движки: {', '.join(sorted(движки)) or '—'}")
        print("Нужно прогнать `jobagent score --again` с ключом Anthropic.")
        return 1

    сравнимые.sort(
        key=lambda v: -abs((v["model"]["score"] or 0) - (v["local"]["score"] or 0))
    )
    расхождения = 0
    print(f"{'лок':>4} {'мод':>4} {'Δ':>5}  вакансия")
    print("-" * 78)
    for v in сравнимые:
        л, м = v["local"]["score"], v["model"]["score"]
        дельта = (м or 0) - (л or 0)
        пометка = ""
        if v["local"]["verdict"] != v["model"]["verdict"]:
            расхождения += 1
            пометка = (
                f"  [{ВЕРДИКТ.get(v['local']['verdict'])} → "
                f"{ВЕРДИКТ.get(v['model']['verdict'])}]"
            )
        print(f"{л if л is not None else '—':>4} {м if м is not None else '—':>4} "
              f"{дельта:+5}  {v['title'][:48]}{пометка}")

    print("-" * 78)
    print(f"вакансий сравнено: {len(сравнимые)}, вердикт разошёлся: {расхождения}")

    print("\n--- обоснования там, где вердикт разошёлся ---")
    for v in сравнимые:
        if v["local"]["verdict"] == v["model"]["verdict"]:
            continue
        print(f"\n{v['title']} ({v['company']})")
        print(f"  локально: {v['local']['reasoning']}")
        print(f"  модель:   {v['model']['reasoning']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
