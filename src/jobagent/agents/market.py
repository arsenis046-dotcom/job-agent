"""Market Analyst — что происходит на рынке по активному профилю поиска.

Это считалка, а не модель: число вакансий, вилки зарплат, частота терминов
и разница с предыдущим снимком получаются из базы арифметикой. Модель здесь
могла бы только пересказать цифры словами, а заодно и приврать.

Частота терминов считается по **числу вакансий**, в которых термин
встретился, а не по числу упоминаний: вакансия, двадцать раз написавшая
«Python», не должна весить как двадцать вакансий.

Важная оговорка про честность: словаря технологий у нас нет, поэтому за
термины берутся латинские слова из текста вакансий плюс многословные
названия из моего профиля («Claude Code»). Это приближение, и в отчёте так
и написано.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
from dataclasses import dataclass, field

from ..db import now
from ..profile import MasterProfile

TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}")

# Латиница, которая встречается в объявлениях, но навыком не является
NOISE = frozenset(
    """a an and or the of to in for with without on at by from as is are be
    you your we our they it this that not no yes all any more most less
    ru com www http https org net info html body div span px id class
    junior middle senior lead team full time part remote office hybrid
    cv hr it ai ml""".split()
)

MIN_TERM_LENGTH = 2


@dataclass
class Snapshot:
    """Состояние рынка на момент замера."""

    profile: str
    platform: str
    taken_at: str
    vacancies: int = 0
    with_salary: int = 0
    salary_p25: int | None = None
    salary_median: int | None = None
    salary_p75: int | None = None
    terms: list[tuple[str, int]] = field(default_factory=list)

    @property
    def terms_dict(self) -> dict[str, int]:
        return dict(self.terms)


@dataclass
class MarketReport:
    current: Snapshot
    previous: Snapshot | None = None
    covered: list[tuple[str, int]] = field(default_factory=list)
    uncovered: list[tuple[str, int]] = field(default_factory=list)

    @property
    def vacancies_delta(self) -> int | None:
        if self.previous is None:
            return None
        return self.current.vacancies - self.previous.vacancies

    def term_delta(self, term: str) -> int | None:
        if self.previous is None:
            return None
        return self.current.terms_dict.get(term, 0) - self.previous.terms_dict.get(term, 0)

    def growing(self, limit: int = 5) -> list[tuple[str, int]]:
        """Термины, которых стало заметно больше, чем в прошлый раз."""
        if self.previous is None:
            return []
        deltas = [
            (term, self.term_delta(term) or 0) for term, _ in self.current.terms
        ]
        return [item for item in sorted(deltas, key=lambda x: -x[1])[:limit] if item[1] > 0]

    def falling(self, limit: int = 5) -> list[tuple[str, int]]:
        if self.previous is None:
            return []
        deltas = [
            (term, self.term_delta(term) or 0) for term in self.previous.terms_dict
        ]
        return [item for item in sorted(deltas, key=lambda x: x[1])[:limit] if item[1] < 0]


def _salary_value(row: sqlite3.Row) -> int | None:
    """Одно число на вакансию: середина вилки, иначе известная граница."""
    low, high = row["salary_from"], row["salary_to"]
    if low and high:
        return (low + high) // 2
    return low or high or None


def _mentions(haystack: str, term: str) -> bool:
    """Термин как отдельное слово: «git» не должен находиться внутри «github»."""
    return re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", haystack) is not None


def _terms(texts: list[str], extra: list[str]) -> list[tuple[str, int]]:
    """Сколько вакансий упоминает каждый термин."""
    counts: dict[str, int] = {}
    for text in texts:
        lowered = text.casefold()
        seen = set()
        for token in TOKEN.findall(text):
            # «AI-разработчик» режется токенизатором на «AI-», и хвост от
            # кириллицы остаётся висеть дефисом — его надо убрать
            token = token.strip("-.").casefold()
            if len(token) >= MIN_TERM_LENGTH and token not in NOISE:
                seen.add(token)
        # многословные названия из профиля токенизатором не ловятся
        seen |= {phrase.casefold() for phrase in extra if phrase.casefold() in lowered}
        for term in seen:
            counts[term] = counts.get(term, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def analyze(
    conn: sqlite3.Connection,
    profile_name: str,
    master: MasterProfile,
    *,
    platform: str = "all",
    top: int = 25,
) -> MarketReport:
    """Снимок рынка по тому, что уже лежит в базе."""
    where = "" if platform == "all" else " WHERE platform = ?"
    params = () if platform == "all" else (platform,)
    rows = conn.execute(f"SELECT * FROM vacancies{where}", params).fetchall()

    salaries = [value for value in (_salary_value(r) for r in rows) if value]
    texts = [
        " ".join(p for p in (r["title"], r["description"]) if p) for r in rows
    ]
    my_skills = master.known_skills
    phrases = [s for s in my_skills if " " in s]

    snapshot = Snapshot(
        profile=profile_name,
        platform=platform,
        taken_at=now(),
        vacancies=len(rows),
        with_salary=len(salaries),
        terms=_terms(texts, phrases)[:top],
    )
    if salaries:
        ordered = sorted(salaries)
        snapshot.salary_median = int(statistics.median(ordered))
        snapshot.salary_p25 = ordered[len(ordered) // 4]
        snapshot.salary_p75 = ordered[(len(ordered) * 3) // 4]

    # «docker» должен попадать в «Docker Compose»: ищем термин словом внутри
    # названий моих навыков, а не только точное совпадение
    mine = " ".join(my_skills).casefold()
    covered = [(t, c) for t, c in snapshot.terms if _mentions(mine, t)]
    uncovered = [(t, c) for t, c in snapshot.terms if not _mentions(mine, t)]

    return MarketReport(
        current=snapshot,
        previous=last_snapshot(conn, profile_name, platform),
        covered=covered,
        uncovered=uncovered,
    )


def last_snapshot(
    conn: sqlite3.Connection, profile_name: str, platform: str
) -> Snapshot | None:
    row = conn.execute(
        "SELECT * FROM market_snapshots WHERE profile = ? AND platform = ? "
        "ORDER BY taken_at DESC, id DESC LIMIT 1",
        (profile_name, platform),
    ).fetchone()
    if row is None:
        return None
    return Snapshot(
        profile=row["profile"],
        platform=row["platform"],
        taken_at=row["taken_at"],
        vacancies=row["vacancies_count"] or 0,
        with_salary=(json.loads(row["raw_json"]) or {}).get("with_salary", 0)
        if row["raw_json"]
        else 0,
        salary_p25=row["salary_p25"],
        salary_median=row["salary_median"],
        salary_p75=row["salary_p75"],
        terms=[tuple(item) for item in json.loads(row["top_skills"] or "[]")],
    )


def save_snapshot(conn: sqlite3.Connection, snapshot: Snapshot) -> int:
    cursor = conn.execute(
        "INSERT INTO market_snapshots (profile, platform, taken_at, vacancies_count, "
        "salary_p25, salary_median, salary_p75, top_skills, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snapshot.profile,
            snapshot.platform,
            snapshot.taken_at,
            snapshot.vacancies,
            snapshot.salary_p25,
            snapshot.salary_median,
            snapshot.salary_p75,
            json.dumps(snapshot.terms, ensure_ascii=False),
            json.dumps({"with_salary": snapshot.with_salary}, ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)
