"""Коннектор SuperJob — единственная площадка, где сквозной путь
«нашли → оценили → откликнулись» работает официально (docs/api_notes.md).

Поиск: GET /2.0/vacancies/ с заголовком X-Api-App-Id.
Отклик: POST /2.0/send_cv_on_vacancy/ (id_cv, id_vacancy, comment)
        плюс Bearer-токен соискателя.
Токен: POST /2.0/oauth2/password/ по логину и паролю своего аккаунта.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..config import Access, require_env
from .base import ApplyResult, Connector, ConnectorError, Vacancy

API = "https://api.superjob.ru/2.0"


class SuperJobConnector(Connector):
    name = "superjob"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._token: str | None = None

    # --- авторизация -------------------------------------------------------

    @property
    def app_headers(self) -> dict[str, str]:
        secret = require_env("SUPERJOB_SECRET", self.config.root)
        if not secret.isascii():
            # значение уходит в заголовок, а там только ASCII: иначе httpx
            # падает в UnicodeEncodeError, по которому ничего не понять.
            # Сам ключ в сообщение не попадает.
            raise ConnectorError(
                "SUPERJOB_SECRET содержит нелатинские символы — в заголовок "
                "такое не уходит. Проверьте, не попал ли лишний символ при "
                "копировании ключа"
            )
        return {"X-Api-App-Id": secret}

    def user_token(self) -> str:
        """Токен соискателя. Пароль уходит только в этот запрос и не логируется."""
        if self._token:
            return self._token
        self.limiter.wait()
        response = self.client.post(
            f"{API}/oauth2/password/",
            headers=self.app_headers,
            data={
                "login": require_env("SUPERJOB_LOGIN", self.config.root),
                "password": require_env("SUPERJOB_PASSWORD", self.config.root),
                "client_id": require_env("SUPERJOB_CLIENT_ID", self.config.root),
                "client_secret": require_env("SUPERJOB_SECRET", self.config.root),
            },
        )
        self._check(response)
        token = response.json().get("access_token")
        if not token:
            raise ConnectorError("superjob: в ответе нет access_token")
        if not str(token).isascii():
            # токен тоже уходит в заголовок; сам токен в сообщение не попадает
            raise ConnectorError(
                "superjob: в access_token нелатинские символы — в заголовок "
                "такое не уходит"
            )
        self._token = token
        return token

    # --- поиск -------------------------------------------------------------

    def search(self) -> Iterator[Vacancy]:
        if self.access is not Access.api:
            return
        profile = self.config.profile
        for keyword in profile.keywords:
            page = 0
            while True:
                params: dict[str, object] = {
                    "keyword": keyword,
                    "count": 100,
                    "page": page,
                }
                if profile.salary_min:
                    params["payment_from"] = profile.salary_min
                    # без этого в выдачу лезут вакансии «зарплата по договорённости»
                    params["no_agreement"] = 1
                data = self._get(
                    f"{API}/vacancies/", params=params, headers=self.app_headers
                ).json()
                for item in data.get("objects", []):
                    yield self._from_api(item)
                if not data.get("more"):
                    break
                page += 1

    def _from_api(self, item: dict) -> Vacancy:
        return Vacancy(
            platform=self.name,
            external_id=str(item["id"]),
            title=item.get("profession") or "",
            url=item.get("link"),
            company=(item.get("client") or {}).get("title") or item.get("firm_name"),
            area=(item.get("town") or {}).get("title"),
            salary_from=item.get("payment_from") or None,
            salary_to=item.get("payment_to") or None,
            currency=item.get("currency"),
            schedule=(item.get("type_of_work") or {}).get("title"),
            employment=(item.get("place_of_work") or {}).get("title"),
            description=item.get("vacancyRichText") or item.get("candidat"),
            published_at=str(item.get("date_published") or "") or None,
            raw=item,
        )

    # --- отклик ------------------------------------------------------------

    def _do_apply(
        self, vacancy: Vacancy, resume_id: str, message: str
    ) -> ApplyResult:
        headers = dict(self.app_headers)
        headers["Authorization"] = f"Bearer {self.user_token()}"
        self.limiter.wait()
        response = self.client.post(
            f"{API}/send_cv_on_vacancy/",
            headers=headers,
            data={
                "id_cv": resume_id,
                "id_vacancy": vacancy.external_id,
                "comment": message,
            },
        )
        if response.status_code == 403:
            # площадка так отвечает и на повторный отклик
            return ApplyResult(
                sent=False,
                status="failed",
                detail="superjob отказал: операция невозможна (возможно, повторный отклик)",
            )
        self._check(response)
        payload = response.json()
        if payload.get("result") is True:
            return ApplyResult(
                sent=True, status="sent", detail="отклик отправлен", response=payload
            )
        return ApplyResult(
            sent=False,
            status="failed",
            detail=f"неожиданный ответ: {payload}",
            response=payload,
        )
