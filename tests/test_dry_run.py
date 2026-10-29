"""Критерий приёмки №1: в dry_run ни один запрос на отправку не уходит."""

from __future__ import annotations

import httpx
import pytest
import respx

from jobagent.config import Access, ApplyMode, Mode
from jobagent.connectors import REGISTRY, Vacancy, build_connector

VACANCY = Vacancy(
    platform="superjob", external_id="26747390", title="AI-разработчик"
)


def _with_platforms(config, **overrides):
    """Конфигурация с подменёнными настройками площадок."""
    platforms = {
        name: platform.model_copy(update=overrides.get(name, {}))
        for name, platform in config.settings.platforms.items()
    }
    return config.model_copy(
        update={"settings": config.settings.model_copy(update={"platforms": platforms})}
    )


@respx.mock
@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_в_dry_run_наружу_ничего_не_уходит(config, name, monkeypatch):
    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    monkeypatch.setenv("HH_ACCESS_TOKEN", "test-token")
    # любой запрос в этом тесте — провал: его не должно быть вовсе
    route = respx.route().mock(side_effect=AssertionError("ушёл запрос в dry_run"))

    connector = build_connector(name, config)
    result = connector.apply(VACANCY, resume_id="1", message="письмо")

    assert config.settings.mode is Mode.dry_run
    assert result.sent is False
    assert result.status == "draft"
    assert route.call_count == 0


@respx.mock
def test_в_approve_отправка_ждёт_подтверждения(config):
    approve = config.model_copy(
        update={"settings": config.settings.model_copy(update={"mode": Mode.approve})}
    )
    route = respx.route().mock(side_effect=AssertionError("отправка без подтверждения"))
    result = build_connector("superjob", approve).apply(VACANCY, "1", "письмо")
    assert (result.sent, result.status) == (False, "awaiting")
    assert route.call_count == 0


@respx.mock
def test_площадка_без_api_откликов_даёт_черновик(auto_config):
    """hh.ru с apply=draft не отправляет даже в auto."""
    draft_only = _with_platforms(
        auto_config, hh={"apply": ApplyMode.draft, "enabled": True}
    )
    route = respx.route().mock(side_effect=AssertionError("отправка при apply=draft"))
    result = build_connector("hh", draft_only).apply(VACANCY, "1", "письмо")
    assert (result.sent, result.status) == (False, "draft")
    assert route.call_count == 0


@respx.mock
def test_выключенная_площадка_не_отправляет(auto_config):
    disabled = _with_platforms(
        auto_config, superjob={"enabled": False, "apply": ApplyMode.api}
    )
    route = respx.route().mock(side_effect=AssertionError("отправка с выключенной площадки"))
    result = build_connector("superjob", disabled).apply(VACANCY, "1", "письмо")
    assert (result.sent, result.status) == (False, "skipped")
    assert route.call_count == 0


@respx.mock
def test_в_auto_отклик_действительно_уходит(auto_config, monkeypatch):
    """Обратная проверка: защита не «выключает» отправку насовсем."""
    monkeypatch.setenv("SUPERJOB_SECRET", "test-secret")
    monkeypatch.setenv("SUPERJOB_LOGIN", "me@example.com")
    monkeypatch.setenv("SUPERJOB_PASSWORD", "pwd")
    monkeypatch.setenv("SUPERJOB_CLIENT_ID", "2345")
    respx.post("https://api.superjob.ru/2.0/oauth2/password/").mock(
        return_value=httpx.Response(200, json={"access_token": "r.000.token"})
    )
    send = respx.post("https://api.superjob.ru/2.0/send_cv_on_vacancy/").mock(
        return_value=httpx.Response(200, json={"result": True})
    )

    result = build_connector("superjob", auto_config).apply(VACANCY, "25903399", "письмо")

    assert (result.sent, result.status) == (True, "sent")
    assert send.call_count == 1
    request = send.calls[0].request
    assert b"id_vacancy=26747390" in request.content
    assert request.headers["X-Api-App-Id"] == "test-secret"
    assert request.headers["Authorization"] == "Bearer r.000.token"


@respx.mock
def test_секретов_нет_в_сообщениях_об_ошибках(auto_config, monkeypatch):
    monkeypatch.setenv("SUPERJOB_SECRET", "очень-секретный-ключ")
    monkeypatch.setenv("SUPERJOB_LOGIN", "me@example.com")
    monkeypatch.setenv("SUPERJOB_PASSWORD", "очень-секретный-пароль")
    monkeypatch.setenv("SUPERJOB_CLIENT_ID", "2345")
    respx.post("https://api.superjob.ru/2.0/oauth2/password/").mock(
        return_value=httpx.Response(401, json={"error": {"code": 401}})
    )
    connector = build_connector("superjob", auto_config)
    with pytest.raises(Exception) as exc:
        connector.apply(VACANCY, "1", "письмо")
    text = str(exc.value)
    assert "секретный" not in text
