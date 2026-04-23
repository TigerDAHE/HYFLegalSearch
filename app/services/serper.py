from typing import Any
import time

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger


logger = get_logger("app.services.serper")


class SerperClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _normalize_query(self, query: str) -> str:
        normalized = " ".join(query.split())
        max_len = max(40, int(self.settings.serper_max_query_length))
        if len(normalized) > max_len:
            normalized = normalized[:max_len]
        return normalized

    def search(self, query: str) -> list[dict[str, Any]]:
        if not self.settings.serper_api_key:
            logger.warning("serper_api_key_missing")
            return []

        query = self._normalize_query(query)
        if not query:
            logger.warning("serper_empty_query")
            return []

        headers = {
            "X-API-KEY": self.settings.serper_api_key,
            "Content-Type": "application/json",
            "User-Agent": "AgenticSearch/0.1",
        }
        payload = {
            "q": query,
            "gl": "cn",
            "hl": "zh-cn",
            "num": self.settings.serper_num_results,
        }

        retries = max(0, int(self.settings.serper_retries))
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with httpx.Client(
                    timeout=self.settings.serper_timeout_seconds,
                    trust_env=self.settings.serper_trust_env,
                    follow_redirects=True,
                ) as client:
                    response = client.post(self.settings.serper_endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                last_error = exc
                # 429 / 5xx: can retry
                if status in (429, 500, 502, 503, 504) and attempt < retries:
                    sleep_s = float(self.settings.serper_retry_backoff_seconds) * (2**attempt)
                    logger.warning(
                        "serper_retry_status query=%s status=%s attempt=%s/%s sleep=%.2fs",
                        query,
                        status,
                        attempt + 1,
                        retries + 1,
                        sleep_s,
                    )
                    time.sleep(sleep_s)
                    continue
                logger.error("serper_status_error query=%s status=%s", query, status)
                return []
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if attempt < retries:
                    sleep_s = float(self.settings.serper_retry_backoff_seconds) * (2**attempt)
                    logger.warning(
                        "serper_retry_network query=%s error=%s attempt=%s/%s sleep=%.2fs",
                        query,
                        exc.__class__.__name__,
                        attempt + 1,
                        retries + 1,
                        sleep_s,
                    )
                    time.sleep(sleep_s)
                    continue
                logger.error("serper_connect_error query=%s error=%s", query, exc)
                return []
            except Exception as exc:
                last_error = exc
                logger.exception("serper_unexpected_error query=%s", query)
                return []
        else:
            if last_error:
                logger.error("serper_failed_after_retries query=%s error=%s", query, last_error)
            return []

        organic = data.get("organic", [])
        results: list[dict[str, Any]] = []
        for item in organic:
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                }
            )
        logger.info("serper_search_ok query=%s results=%s", query, len(results))
        return results


serper_client = SerperClient()
