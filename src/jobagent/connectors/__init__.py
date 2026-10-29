"""Реестр коннекторов. Добавить площадку = добавить файл и строку здесь."""

from __future__ import annotations

from ..config import Config
from .base import ApplyResult, Connector, ConnectorError, RateLimiter, Vacancy
from .hh import HHConnector
from .superjob import SuperJobConnector
from .telegram import TelegramConnector

REGISTRY: dict[str, type[Connector]] = {
    HHConnector.name: HHConnector,
    SuperJobConnector.name: SuperJobConnector,
    TelegramConnector.name: TelegramConnector,
}

__all__ = [
    "ApplyResult",
    "Connector",
    "ConnectorError",
    "RateLimiter",
    "Vacancy",
    "REGISTRY",
    "build_connector",
    "enabled_connectors",
]


def build_connector(name: str, config: Config, **kwargs) -> Connector:
    try:
        cls = REGISTRY[name]
    except KeyError as exc:
        raise ConnectorError(f"неизвестная площадка {name!r}") from exc
    return cls(config, **kwargs)


def enabled_connectors(config: Config, **kwargs) -> list[Connector]:
    """Только включённые в settings.yaml площадки, у которых есть коннектор."""
    return [
        build_connector(name, config, **kwargs)
        for name, platform in config.settings.platforms.items()
        if platform.enabled and name in REGISTRY
    ]
