"""Обращение к модели — в одном месте.

Здесь единственная точка, где проект разговаривает с Anthropic API. Всё
остальное получает типизированный результат или понятную ошибку.

Три решения, которые стоит знать:

1. **Структурированный вывод через `messages.parse`**, а не «попроси JSON
   и распарси». Схема — та же pydantic-модель, что в остальном проекте.
2. **Принудительный вызов инструмента не используется.** На Sonnet 5.5
   и Opus 5.5 `tool_choice` со значением `any` или `tool` отвечает ошибкой
   400, так что прежний приём «заставить модель вызвать инструмент ради
   JSON» больше не работает. Структурированный вывод решает ту же задачу.
3. **Отказ модели — не сбой программы.** Если сработали защитные
   классификаторы (`stop_reason == "refusal"`), поднимается `LLMRefused`,
   и вызывающий переключается на локальный движок, а не падает.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from ..config import Config, env

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """Ключа нет — работаем локальными движками."""


class LLMRefused(RuntimeError):
    """Модель отказалась отвечать. Для нас это повод оценить локально."""


class LLMError(RuntimeError):
    """Остальные неприятности с API, уже с понятным текстом."""


class LLM:
    """Тонкая обёртка: модель из конфига, схема на входе, объект на выходе."""

    def __init__(self, config: Config, *, heavy: bool = False) -> None:
        self.config = config
        settings = config.settings.llm
        self.model = (settings.model_heavy or settings.model) if heavy else settings.model
        self.max_tokens = settings.max_tokens
        self._client = None

    @property
    def available(self) -> bool:
        return bool(env("ANTHROPIC_API_KEY", self.config.root))

    @property
    def client(self):
        if self._client is None:
            if not self.available:
                raise LLMUnavailable(
                    "не задан ANTHROPIC_API_KEY — движки на модели недоступны, "
                    "работают локальные"
                )
            import anthropic

            self._client = anthropic.Anthropic(
                api_key=env("ANTHROPIC_API_KEY", self.config.root)
            )
        return self._client

    def parse(
        self,
        schema: type[T],
        *,
        system: str,
        prompt: str,
        effort: str = "low",
    ) -> T:
        """Ответ модели, разобранный в экземпляр `schema`.

        `effort` по умолчанию `low`: оценка вакансии и короткое письмо —
        не те задачи, где нужно глубокое размышление, а платим мы за токены.
        """
        import anthropic

        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": effort},
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(self._explain(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("нет связи с Anthropic API") from exc

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise LLMRefused(f"модель отказалась отвечать (категория: {category})")
        if response.stop_reason == "max_tokens":
            raise LLMError(
                f"ответ не поместился в {self.max_tokens} токенов — "
                "увеличить llm.max_tokens в settings.yaml"
            )

        parsed = response.parsed_output
        if parsed is None:
            raise LLMError("модель вернула ответ не по схеме")
        return parsed

    @staticmethod
    def _explain(exc) -> str:
        """Понятное сообщение, без ключа внутри."""
        code = getattr(exc, "status_code", None)
        if code == 401:
            return "Anthropic API: ключ не принят (401)"
        if code == 429:
            return "Anthropic API: лимит запросов (429), нужна пауза"
        if code and code >= 500:
            return f"Anthropic API недоступен ({code})"
        return f"Anthropic API: ошибка {code}"
