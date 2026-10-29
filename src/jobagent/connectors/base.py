"""Общий интерфейс коннекторов площадок.

Главное в этом файле — метод `apply()`. Он не абстрактный: отправку наружу
делает только он, и он же проверяет режим и настройки площадки. Коннектор
реализует `_do_apply()` и не имеет права вызывать его сам. Так требование
«в dry_run ни один запрос на отправку не уходит» держится в одном месте,
а не в каждом коннекторе по отдельности.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..config import Access, ApplyMode, Config, Mode, PlatformSettings, user_agent


class ConnectorError(RuntimeError):
    """Площадка ответила ошибкой или не настроена."""


class Vacancy(BaseModel):
    """Вакансия в том виде, в каком её отдаёт любой коннектор."""

    platform: str
    external_id: str
    title: str
    url: str | None = None
    company: str | None = None
    area: str | None = None
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str | None = None
    schedule: str | None = None
    employment: str | None = None
    description: str | None = None
    published_at: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_text(self) -> bool:
        """Можно ли эту вакансию честно оценивать, или мы знаем лишь заголовок."""
        return bool(self.description)


class ApplyResult(BaseModel):
    sent: bool
    status: str  # sent / draft / awaiting / failed / skipped
    detail: str | None = None
    response: dict[str, Any] | None = None


class RateLimiter:
    """Пауза между запросами и потолок в минуту — чтобы не злить площадку."""

    def __init__(self, delay_seconds: float, max_per_minute: int) -> None:
        self.delay = delay_seconds
        self.max_per_minute = max_per_minute
        self._times: list[float] = []

    def wait(self) -> None:
        now = time.monotonic()
        self._times = [t for t in self._times if now - t < 60]
        if len(self._times) >= self.max_per_minute:
            time.sleep(60 - (now - self._times[0]))
            now = time.monotonic()
            self._times = [t for t in self._times if now - t < 60]
        elif self._times:
            gap = now - self._times[-1]
            if gap < self.delay:
                time.sleep(self.delay - gap)
        self._times.append(time.monotonic())


class Connector(ABC):
    """Базовый коннектор. Новая площадка не требует правок в pipeline."""

    name: str = "base"

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        self.config = config
        self.platform: PlatformSettings = config.settings.platform(self.name)
        self.limiter = RateLimiter(
            config.settings.limits.request_delay_seconds,
            config.settings.limits.max_requests_per_minute,
        )
        self._client = client
        self._own_client = client is None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=30.0,
                headers={"User-Agent": user_agent(self.config.root)},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._own_client:
            self._client.close()
            self._client = None

    @property
    def can_apply_via_api(self) -> bool:
        return self.platform.apply is ApplyMode.api

    @property
    def access(self) -> Access:
        return self.platform.access

    # --- поиск -------------------------------------------------------------

    @abstractmethod
    def search(self) -> Iterator[Vacancy]:
        """Вакансии по активному профилю поиска, уже в общем виде."""

    # --- отправка ----------------------------------------------------------

    def apply(self, vacancy: Vacancy, resume_id: str, message: str) -> ApplyResult:
        """Единственная дверь наружу. Сначала запреты, потом запрос."""
        mode = self.config.settings.mode

        if mode is Mode.dry_run:
            return ApplyResult(
                sent=False,
                status="draft",
                detail="режим dry_run: отправки нет, письмо сохранено черновиком",
            )
        if not self.platform.enabled:
            return ApplyResult(
                sent=False, status="skipped", detail=f"площадка {self.name} выключена"
            )
        if not self.can_apply_via_api:
            return ApplyResult(
                sent=False,
                status="draft",
                detail=(
                    f"у {self.name} apply={self.platform.apply.value}: "
                    "API откликов недоступен, черновик и ссылка"
                ),
            )
        if mode is Mode.approve:
            return ApplyResult(
                sent=False,
                status="awaiting",
                detail="режим approve: ждёт подтверждения",
            )

        self.limiter.wait()
        return self._do_apply(vacancy, resume_id, message)

    @abstractmethod
    def _do_apply(
        self, vacancy: Vacancy, resume_id: str, message: str
    ) -> ApplyResult:
        """Настоящий запрос. Вызывается только из `apply()`."""

    # --- вспомогательное ---------------------------------------------------

    def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.limiter.wait()
        response = self.client.get(url, **kwargs)
        self._check(response)
        return response

    def _check(self, response: httpx.Response) -> None:
        """Понятные сообщения вместо трассировки — и ни слова о токене."""
        if response.status_code < 400:
            return
        code = response.status_code
        if code in (401, 403):
            raise ConnectorError(
                f"{self.name}: {code} — доступ не выдан. "
                "Проверить регистрацию приложения и токен (см. docs/api_notes.md)"
            )
        if code == 429:
            raise ConnectorError(f"{self.name}: 429 — слишком часто, нужна пауза")
        if code >= 500:
            raise ConnectorError(f"{self.name}: {code} — площадка недоступна")
        raise ConnectorError(f"{self.name}: {code} {response.text[:200]}")
