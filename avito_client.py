from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from config import settings


class AvitoClient:
    """
    Упрощённый клиент Avito для поиска резюме/объявлений о работе.

    В бою обычно используют официальный SDK и OAuth.
    Здесь реализован минимальный вариант на basis access token:
    если не заданы AVITO_CLIENT_ID/AVITO_CLIENT_SECRET/AVITO_TOKEN,
    клиент вернёт пустой список и не будет ломать работу бота.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        # В реальной интеграции токен нужно получать по client_id/client_secret.
        self.token = token or settings.avito_token
        self.base_url = (base_url or settings.avito_base_url).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "User-Agent": "GWorkBot/1.0 (avito integration)",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def search_candidates(
        self,
        query: str,
        location: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
        timeout: float = 15.0,
    ) -> List[Dict[str, Any]]:
        """
        Поиск объявлений на Avito в разделе работы по ключевым словам.

        Конкретные параметры и схема ответа зависят от версии API.
        Поэтому тут максимально безопасный маппинг: берём поля, если они есть,
        и приводим к единому виду для модели Candidate.
        """
        if not self.token:
            return []

        params: Dict[str, Any] = {
            "q": query,
            "page": page,
            "per_page": per_page,
        }
        if location:
            params["location"] = location

        # В официальном API эндпоинт может отличаться.
        url = f"{self.base_url}/core/v1/items"
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url, headers=self._headers(), params=params)
                resp.raise_for_status()
        except Exception:
            return []

        data = resp.json()
        items = data.get("items") or data.get("result") or []

        results: List[Dict[str, Any]] = []
        for obj in items:
            if not isinstance(obj, dict):
                continue

            title = obj.get("title") or obj.get("description") or "Кандидат Avito"
            city = ""
            loc = obj.get("location") or {}
            if isinstance(loc, dict):
                city = loc.get("city") or loc.get("address") or ""
            link = obj.get("url") or obj.get("link") or ""

            description = obj.get("description") or ""

            results.append(
                {
                    "id": obj.get("id"),
                    "name": title,
                    "city": city,
                    "text": description,
                    "link": link,
                    "raw": obj,
                }
            )

        return results

