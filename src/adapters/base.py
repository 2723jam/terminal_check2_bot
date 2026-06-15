from __future__ import annotations

from abc import ABC, abstractmethod

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.models import CheckFailure, PortConfig, TerminalStatusEvent


class BaseAdapter(ABC):
    adapter_name = "base"

    def __init__(self, timeout_seconds: float = 20.0, user_agent: str | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or "terminal_check2_bot/1.0"
        self.last_failures: list[CheckFailure] = []

    def reset_failures(self) -> None:
        self.last_failures = []

    def record_failure(self, port_code: str, source_url: str | None, error: Exception) -> None:
        self.last_failures.append(CheckFailure(port_code=port_code, source_url=source_url, error=str(error)))

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def fetch(self, url: str) -> str:
        headers = {"User-Agent": self.user_agent}
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    @abstractmethod
    async def check(self, port: PortConfig) -> list[TerminalStatusEvent]:
        raise NotImplementedError

    @staticmethod
    def usable_source_urls(port: PortConfig) -> list[str]:
        return [url for url in port.source_urls if url and not url.strip().upper().startswith("TODO")]
