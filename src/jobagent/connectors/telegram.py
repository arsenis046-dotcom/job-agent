"""Коннектор к публичным телеграм-каналам с вакансиями.

Почему это законный путь, в отличие от парсинга hh.ru: Telegram сам
предоставляет интерфейс для чтения (MTProto, core.telegram.org), и мы
читаем через него под моим аккаунтом те каналы, на которые я и так могу
зайти. Это «официальный API» в том смысле, который требует ТЗ, а не обход
закрытого доступа. Веб-превью `t.me/s/...` мы не парсим.

Откликов тут нет и быть не может: в каналах отвечают письмом или в личку
человеку. Поэтому у площадки `apply: draft` — агент готовит письмо, а
отправляю я сам. Это не недоделка, а природа канала.

Вход в аккаунт выполняется один раз командой `jobagent telegram login`:
Telethon спросит телефон и код. Ни телефон, ни код, ни пароль агент не
вводит и не хранит — остаётся только файл сессии, и он в .gitignore.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Access, env
from .base import ApplyResult, Connector, ConnectorError, Vacancy
from .telegram_posts import разобрать

# Сколько дней назад смотреть при обычном запуске
ПЕРИОД_ДНЕЙ = 7
# Сколько сообщений максимум читать в одном канале за запуск
ПРЕДЕЛ_СООБЩЕНИЙ = 200


class TelegramConnector(Connector):
    name = "telegram"

    def __init__(self, *args, client=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._client = client
        self._seen: set[str] = set()

    # --- доступ ------------------------------------------------------------

    @property
    def session_path(self) -> Path:
        return self.config.root / (
            env("TELEGRAM_SESSION", self.config.root) or "output/telegram.session"
        )

    def _credentials(self) -> tuple[int, str]:
        api_id = env("TELEGRAM_API_ID", self.config.root)
        api_hash = env("TELEGRAM_API_HASH", self.config.root)
        if not api_id or not api_hash:
            raise ConnectorError(
                "telegram: не заданы TELEGRAM_API_ID и TELEGRAM_API_HASH. "
                "Получить на https://my.telegram.org → API development tools, "
                "затем выполнить `jobagent telegram login`"
            )
        try:
            return int(api_id), api_hash
        except ValueError as exc:
            raise ConnectorError("telegram: TELEGRAM_API_ID должен быть числом") from exc

    def client(self):
        """Клиент Telethon. Создаётся лениво: без входа он не нужен."""
        if self._client is None:
            from telethon.sync import TelegramClient

            api_id, api_hash = self._credentials()
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            self._client = TelegramClient(str(self.session_path), api_id, api_hash)
        return self._client

    # --- поиск -------------------------------------------------------------

    @property
    def channels(self) -> list[str]:
        return [c.lstrip("@") for c in self.platform.channels]

    def search(self) -> Iterator[Vacancy]:
        if self.access is not Access.api:
            return
        if not self.channels:
            raise ConnectorError(
                "telegram: не заданы каналы. Добавить в config/settings.yaml "
                "в platforms.telegram.channels"
            )

        since = datetime.now(timezone.utc) - timedelta(days=ПЕРИОД_ДНЕЙ)
        for channel in self.channels:
            try:
                for post in self._posts(channel, since):
                    vacancy = self._to_vacancy(channel, post)
                    if vacancy is not None:
                        yield vacancy
            except ConnectorError:
                raise
            except Exception as exc:  # канал мог быть удалён или закрыт
                raise ConnectorError(
                    f"telegram: канал @{channel} не прочитался ({type(exc).__name__})"
                ) from exc

    def _posts(self, channel: str, since: datetime) -> list[dict]:
        """Сообщения канала новее `since`. Отдельный метод — чтобы подменять в тестах."""
        client = self.client()
        посты: list[dict] = []
        with client:
            for message in client.iter_messages(channel, limit=ПРЕДЕЛ_СООБЩЕНИЙ):
                if message.date and message.date < since:
                    break
                text = message.text or ""
                if text.strip():
                    посты.append(
                        {
                            "id": message.id,
                            "date": message.date.isoformat() if message.date else None,
                            "text": text,
                        }
                    )
                self.limiter.wait()
        return посты

    def _to_vacancy(self, channel: str, post: dict) -> Vacancy | None:
        разбор = разобрать(post["text"])
        if not разбор.is_vacancy:
            return None

        # одну и ту же вакансию перепечатывают несколько каналов: считаем
        # отпечаток по тексту, чтобы не плодить дубликаты в пределах запуска
        отпечаток = hashlib.sha256(
            " ".join(post["text"].split()).casefold().encode("utf-8")
        ).hexdigest()[:16]
        if отпечаток in self._seen:
            return None
        self._seen.add(отпечаток)

        return Vacancy(
            platform=self.name,
            external_id=f"{channel}/{post['id']}",
            title=разбор.title,
            url=f"https://t.me/{channel}/{post['id']}",
            company=f"@{channel}",
            salary_from=разбор.salary_from,
            salary_to=разбор.salary_to,
            currency=разбор.currency,
            description=post["text"],
            published_at=post["date"],
            raw={
                "channel": channel,
                "contacts": разбор.contacts,
                "links": разбор.links,
                "fingerprint": отпечаток,
            },
        )

    # --- отклик ------------------------------------------------------------

    def _do_apply(self, vacancy: Vacancy, resume_id: str, message: str) -> ApplyResult:
        """Недостижимо: у площадки apply=draft, и база это проверяет раньше."""
        raise ConnectorError(
            "telegram: отклик через канал невозможен — в каналах отвечают "
            "письмом или в личку. Письмо готово, отправить нужно самому"
        )
