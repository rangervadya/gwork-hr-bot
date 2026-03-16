from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from config import settings


class SuperJobClient:
    """
    Минимальный клиент SuperJob для поиска резюме/кандидатов.
    Использует только app-key (X-Api-App-Id) и публичные методы.
    Для продового использования понадобится полноценный OAuth.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or settings.superjob_api_key
        self.base_url = (base_url or settings.superjob_base_url).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Api-App-Id": self.api_key,
        }

    def search_resumes(
        self,
        keyword: str,
        town: Optional[int] = None,
        page: int = 0,
        count: int = 20,
        timeout: float = 15.0,
    ) -> List[Dict[str, Any]]:
        """
        Упрощённый пример поиска резюме.
        В реальной системе нужно аккуратно маппить города (параметр town) и фильтры.
        """
        if not self.api_key:
            return []

        params: Dict[str, Any] = {
            "keyword": keyword,
            "page": page,
            "count": count,
        }
        if town is not None:
            params["town"] = town

        url = f"{self.base_url}/cv/search/"
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url, headers=self._headers(), params=params)
                resp.raise_for_status()
        except Exception:
            return []

        data = resp.json()
        objects = data.get("objects") or []
        result: List[Dict[str, Any]] = []
        for obj in objects:
            result.append(
                {
                    "id": obj.get("id"),
                    "name": obj.get("name") or obj.get("profession"),
                    "town": obj.get("town", {}).get("title") if obj.get("town") else "",
                    "age": obj.get("age"),
                    "experience": obj.get("experience", []),
                    "salary": obj.get("salary"),
                    "link": obj.get("link") or "",
                    "raw": obj,
                }
            )
        return result

