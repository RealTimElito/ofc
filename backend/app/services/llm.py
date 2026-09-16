"""OpenAI-compatible chat client for air-gapped endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.models import LlmProfile
from app.services.crypto import decrypt_secret


@dataclass
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    system_prompt: str
    timeout_seconds: int


def config_from_settings(settings: Optional[Settings] = None) -> LlmConfig:
    settings = settings or get_settings()
    return LlmConfig(
        base_url=settings.llm_base_url.rstrip("/"),
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        system_prompt=settings.llm_system_prompt,
        timeout_seconds=settings.llm_timeout_seconds,
    )


def config_from_profile(profile: LlmProfile, settings: Optional[Settings] = None) -> LlmConfig:
    settings = settings or get_settings()
    api_key = decrypt_secret(profile.api_key_enc) if profile.api_key_enc else settings.llm_api_key
    return LlmConfig(
        base_url=profile.base_url.rstrip("/"),
        api_key=api_key,
        model=profile.model,
        temperature=profile.temperature,
        max_tokens=profile.max_tokens,
        system_prompt=profile.system_prompt or settings.llm_system_prompt,
        timeout_seconds=settings.llm_timeout_seconds,
    )


class LlmClient:
    def __init__(self, config: LlmConfig):
        self.config = config

    async def chat(
        self,
        user_prompt: str,
        *,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        url = f"{self.config.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt or self.config.system_prompt,
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = (exc.response.text or "").strip()[:400]
                raise RuntimeError(
                    f"LLM request failed ({exc.response.status_code}) for model "
                    f"{self.config.model!r} at {url}"
                    + (f": {detail}" if detail else "")
                ) from exc
            except httpx.RequestError as exc:
                raise RuntimeError(
                    f"LLM unreachable at {url} (model {self.config.model!r}): {exc}"
                ) from exc
            data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response shape: {data!r}") from exc

    async def ping(self) -> dict[str, Any]:
        """Lightweight connectivity check against /models if available."""
        url = f"{self.config.base_url}/models"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, headers=headers)
            model_count: int | None = None
            try:
                payload = response.json()
                if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                    model_count = len(payload["data"])
                elif isinstance(payload, list):
                    model_count = len(payload)
            except Exception:  # noqa: BLE001
                model_count = None
            return {
                "ok": response.status_code < 400,
                "status_code": response.status_code,
                "model_count": model_count,
                "body_preview": response.text[:500],
            }
