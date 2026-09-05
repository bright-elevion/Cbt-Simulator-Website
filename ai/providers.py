from __future__ import annotations

import os
from typing import Any


class AIProviderError(RuntimeError):
    """Raised when all configured AI providers fail."""

    def __init__(self, message: str, attempts: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.attempts = attempts or []


def _timeout_seconds() -> float:
    try:
        return max(5.0, float(os.getenv("AI_PROVIDER_TIMEOUT_SECONDS", "25")))
    except (TypeError, ValueError):
        return 25.0


def _enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _safe_error_details(error: Exception) -> str:
    details = [f"{type(error).__name__}: {error}"]
    cause = getattr(error, "__cause__", None)
    if cause is not None:
        details.append(f"cause={type(cause).__name__}: {cause}")
    context = getattr(error, "__context__", None)
    if context is not None and context is not cause:
        details.append(f"context={type(context).__name__}: {context}")
    return " | ".join(details)[:800]


def _response_payload(response: Any) -> Any:
    """Convert SDK responses to a plain object when possible."""
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if isinstance(response, (dict, list, str)):
        return response
    return getattr(response, "__dict__", {}) or {}


def _extract_text(value: Any) -> str | None:
    """Extract text from OpenAI, Gemini, and gateway response shapes."""
    if isinstance(value, str) and value.strip():
        return value.strip()

    if isinstance(value, list):
        parts = [_extract_text(item) for item in value]
        parts = [part for part in parts if part]
        return "\n".join(parts) if parts else None

    if not isinstance(value, dict):
        for attribute in ("content", "text", "response", "output"):
            if hasattr(value, attribute):
                text = _extract_text(getattr(value, attribute))
                if text:
                    return text
        if hasattr(value, "message"):
            return _extract_text(getattr(value, "message"))
        return None

    choices = value.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        message = (
            first.get("message")
            if isinstance(first, dict)
            else getattr(first, "message", None)
        )
        text = _extract_text(message)
        if text:
            return text
        text = (
            _extract_text(first.get("text"))
            if isinstance(first, dict)
            else _extract_text(getattr(first, "text", None))
        )
        if text:
            return text

    for key in (
        "output_text",
        "response",
        "answer",
        "output",
        "content",
        "text",
    ):
        text = _extract_text(value.get(key))
        if text:
            return text

    for key in ("data", "result", "message", "candidates", "parts"):
        text = _extract_text(value.get(key))
        if text:
            return text

    return None


def _openai_text(
    api_key: str,
    base_url: str | None,
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
) -> str:
    from openai import OpenAI

    options: dict[str, Any] = {
        "api_key": api_key,
        "timeout": _timeout_seconds(),
        "max_retries": 0,
    }

    normalized_base_url = str(base_url or "").strip().strip('"').strip("'")
    if normalized_base_url.lower() in {"", "none", "null"}:
        normalized_base_url = "https://api.openai.com/v1"
    elif not normalized_base_url.startswith(("http://", "https://")):
        raise AIProviderError(
            "Invalid OpenAI base URL; it must begin with http:// or https://."
        )

    options["base_url"] = normalized_base_url.rstrip("/")

    client = OpenAI(**options)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )

    payload = _response_payload(response)
    text = _extract_text(payload)
    if text:
        return text

    keys = sorted(payload.keys()) if isinstance(payload, dict) else []
    raise AIProviderError(
        "The OpenAI provider returned no usable text "
        f"(response keys: {keys[:20]})"
    )


def _gemini_text(
    api_key: str,
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Generate text with the supported Google Gen AI Python SDK."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=int(_timeout_seconds( ) * 1000),
        ),
    )

    try:
        model_name = str(model or '').strip().removeprefix('models/')

        response = client.models.generate_content(
            model=model_name,
            contents=(
                f"System instructions:\n{system_prompt}\n\n"
                f"User request:\n{user_message}"
            ),
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )

        text = _extract_text(getattr(response, 'text', None))
        if not text:
            text = _extract_text(_response_payload(response))
        if text:
            return text

        payload = _response_payload(response)
        keys = sorted(payload.keys()) if isinstance(payload, dict) else []
        raise AIProviderError(
            'The Gemini provider returned no usable text '
            f'(response keys: {keys[:20]})'
        )
    finally:
        close = getattr(client, 'close', None)
        if callable(close):
            close()

def _configured_providers() -> list[dict[str, str | None]]:

    primary = os.getenv("AI_PRIMARY_PROVIDER", "openai").strip().lower()
    fallback = os.getenv("AI_FALLBACK_PROVIDER", "gemini").strip().lower()

    names = [primary]
    if _enabled("AI_FALLBACK_ENABLED", True) and fallback not in names:
        names.append(fallback)

    providers: list[dict[str, str | None]] = []

    for name in names:
        if name == "openai":
            providers.append({
                "name": "openai",
                "key": os.getenv("OPENAI_API_KEY", "").strip(),
                "base_url": os.getenv(
                    "OPENAI_BASE_URL",
                    "https://api.openai.com/v1",
                ).strip(),
                "model": os.getenv(
                    "OPENAI_MODEL",
                    os.getenv("AI_MODEL", "gpt-4o-mini"),
                ).strip(),
            })
        elif name == "gemini":
            providers.append({
                "name": "gemini",
                "key": os.getenv("GEMINI_API_KEY", "").strip(),
                "base_url": None,
                "model": os.getenv(
                    "GEMINI_MODEL",
                    "gemini-2.5-flash-lite",
                ).strip(),
            })
        elif name == "agentrouter":
            providers.append({
                "name": "agentrouter",
                "key": os.getenv("AGENTROUTER_API_KEY", "").strip(),
                "base_url": os.getenv(
                    "AGENTROUTER_BASE_URL",
                    "https://agentrouter.org/v1",
                ).strip(),
                "model": os.getenv(
                    "AGENTROUTER_MODEL",
                    os.getenv("AI_MODEL", "gpt-4o-mini"),
                ).strip(),
            })

    return providers


def generate_text(
    *,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.2,
    max_tokens: int = 2000,
):
    """Try the primary provider once, then the configured fallback once."""
    attempts: list[dict[str, str]] = []

    for provider in _configured_providers():
        provider_name = str(provider["name"])
        api_key = str(provider["key"] or "")

        if not api_key:
            attempts.append({
                "provider": provider_name,
                "error": "API key is not configured",
            })
            continue

        try:
            if provider_name == "gemini":
                text = _gemini_text(
                    api_key=api_key,
                    model=str(provider["model"]),
                    system_prompt=system_prompt,
                    user_message=user_message,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            else:
                text = _openai_text(
                    api_key=api_key,
                    base_url=provider["base_url"],
                    model=str(provider["model"]),
                    system_prompt=system_prompt,
                    user_message=user_message,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

            return text, provider_name
        except Exception as error:
            attempts.append({
                "provider": provider_name,
                "error": _safe_error_details(error),
            })

    raise AIProviderError(
        "No configured AI provider could complete the request.",
        attempts=attempts,
    )
