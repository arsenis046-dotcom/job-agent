"""Коннектор hh.ru.

Публичный поиск закрыт с апреля 2026 (см. docs/api_notes.md), поэтому
у площадки три режима доступа:

- `api`    — приложение на dev.hh.ru одобрено, работает поиск и отклики;
- `rss`    — официальный публичный RSS поиска: заголовок, ссылка, дата.
             Оценивать такие вакансии нечем, это видно по `Vacancy.has_text`;
- `manual` — вакансии приносит пользователь, коннектор ничего не ищет.

Страницу вакансии мы не парсим сознательно: это обход закрытого доступа,
а по ТЗ так нельзя.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from xml.etree import ElementTree

from ..config import Access, env
from .base import ApplyResult, Connector, ConnectorError, Vacancy

API = "https://api.hh.ru"
RSS = "https://hh.ru/search/vacancy/rss"
VACANCY_ID = re.compile(r"/vacancy/(\d+)")


class HHConnector(Connector):
    name = "hh"

    # --- поиск -------------------------------------------------------------

    def search(self) -> Iterator[Vacancy]:
        if self.access is Access.api:
            yield from self._search_api()
        elif self.access is Access.rss:
            yield from self._search_rss()
        else:
            return

    def _token(self) -> str:
        token = env("HH_ACCESS_TOKEN", self.config.root)
        if not token:
            raise ConnectorError(
                "hh: access=api, но HH_ACCESS_TOKEN не задан. "
                "Либо зарегистрировать приложение на dev.hh.ru, "
                "либо переключить platforms.hh.access на rss"
            )
        return token

    def _search_api(self) -> Iterator[Vacancy]:
        headers = {"Authorization": f"Bearer {self._token()}"}
        for keyword in self.config.profile.keywords:
            page = 0
            while True:
                params = {"text": keyword, "per_page": 50, "page": page}
                data = self._get(
                    f"{API}/vacancies", params=params, headers=headers
                ).json()
                for item in data.get("items", []):
                    yield self._from_api(item)
                page += 1
                if page >= min(data.get("pages", 0), 20):
                    break

    def _search_rss(self) -> Iterator[Vacancy]:
        for keyword in self.config.profile.keywords:
            text = self._get(RSS, params={"text": keyword}).text
            root = ElementTree.fromstring(text)
            for item in root.iterfind("./channel/item"):
                vacancy = self._from_rss(item)
                if vacancy is not None:
                    yield vacancy

    def _from_api(self, item: dict) -> Vacancy:
        salary = item.get("salary") or {}
        employer = item.get("employer") or {}
        return Vacancy(
            platform=self.name,
            external_id=str(item["id"]),
            title=item.get("name", ""),
            url=item.get("alternate_url"),
            company=employer.get("name"),
            area=(item.get("area") or {}).get("name"),
            salary_from=salary.get("from"),
            salary_to=salary.get("to"),
            currency=salary.get("currency"),
            schedule=(item.get("schedule") or {}).get("name"),
            employment=(item.get("employment") or {}).get("name"),
            description=item.get("snippet", {}).get("responsibility"),
            published_at=item.get("published_at"),
            raw=item,
        )

    def _from_rss(self, item: ElementTree.Element) -> Vacancy | None:
        link = (item.findtext("link") or "").strip()
        match = VACANCY_ID.search(link)
        if not match:
            return None
        return Vacancy(
            platform=self.name,
            external_id=match.group(1),
            title=(item.findtext("title") or "").strip(),
            url=link,
            published_at=(item.findtext("pubDate") or "").strip() or None,
            # описания в RSS нет — вакансия попадёт в отчёт без оценки
            description=None,
        )

    # --- отклик ------------------------------------------------------------

    def _do_apply(
        self, vacancy: Vacancy, resume_id: str, message: str
    ) -> ApplyResult:
        """POST /negotiations. Доступен только с токеном соискателя.

        На вакансии с обязательным тестом отклик через API невозможен —
        площадка ответит ошибкой, и это нормальный исход, а не сбой.
        """
        self.limiter.wait()
        response = self.client.post(
            f"{API}/negotiations",
            headers={"Authorization": f"Bearer {self._token()}"},
            data={
                "vacancy_id": vacancy.external_id,
                "resume_id": resume_id,
                "message": message,
            },
        )
        if response.status_code in (201, 204):
            return ApplyResult(sent=True, status="sent", detail="отклик отправлен")
        if response.status_code == 403:
            return ApplyResult(
                sent=False,
                status="failed",
                detail="hh отказал: возможно, обязательный тест или уже откликались",
            )
        self._check(response)
        return ApplyResult(sent=True, status="sent", detail=str(response.status_code))
