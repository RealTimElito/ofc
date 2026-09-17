"""OpenAI-compatible chat client for air-gapped endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.models import LlmProfile
from app.services.crypto import decrypt_secret


class LlmCancelled(Exception):
    """Raised when cancel_check trips during an in-flight LLM HTTP request."""


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
        cancel_check: Optional[Callable[[], bool]] = None,
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
                response = await self._post_with_cancel(
                    client,
                    url,
                    payload=payload,
                    headers=headers,
                    cancel_check=cancel_check,
                )
                response.raise_for_status()
            except LlmCancelled:
                raise
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

    async def _post_with_cancel(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
        cancel_check: Optional[Callable[[], bool]],
    ) -> httpx.Response:
        """POST, optionally aborting the HTTP request when cancel_check trips."""
        if cancel_check is None:
            return await client.post(url, json=payload, headers=headers)

        post_task = asyncio.create_task(client.post(url, json=payload, headers=headers))

        async def _watch_cancel() -> None:
            while True:
                if cancel_check():
                    return
                await asyncio.sleep(0.2)

        watch_task = asyncio.create_task(_watch_cancel())
        try:
            done, _pending = await asyncio.wait(
                {post_task, watch_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if watch_task in done and post_task not in done:
                post_task.cancel()
                try:
                    await post_task
                except (asyncio.CancelledError, httpx.HTTPError, OSError):
                    pass
                # Force-close transport so the peer stops generating if possible.
                await client.aclose()
                raise LlmCancelled("cancelled during LLM request")
            watch_task.cancel()
            try:
                await watch_task
            except asyncio.CancelledError:
                pass
            return post_task.result()
        except LlmCancelled:
            raise
        except Exception:
            if not post_task.done():
                post_task.cancel()
                try:
                    await post_task
                except (asyncio.CancelledError, httpx.HTTPError, OSError):
                    pass
            if not watch_task.done():
                watch_task.cancel()
                try:
                    await watch_task
                except asyncio.CancelledError:
                    pass
            raise

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
