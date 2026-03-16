from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from config import settings


class HHClient:
    """
    Упрощённый клиент hh.ru для поиска резюме.

    Для полноценной работы обычно требуется авторизация работодателя.
    В этом MVP используем токен из переменной окружения HH_API_TOKEN,
    при его отсутствии просто возвращаем пустой список.
    """

    def __init__(
        self,
        api_token: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.api_token = api_token or settings.hh_api_token
        self.base_url = (base_url or settings.hh_base_url).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "User-Agent": "GWorkBot/1.0 (hh.ru integration)",
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def search_resumes(
        self,
        keyword: str,
        area: Optional[str] = None,
        page: int = 0,
        per_page: int = 20,
        timeout: float = 15.0,
    ) -> List[Dict[str, Any]]:
        """
        Поиск резюме на hh.ru по ключевому слову.

        Структура ответа hh.ru может отличаться, поэтому маппинг сделан
        максимально защитным: берём только то, что есть, остальное заполняем
        безопасными значениями.
        """
        if not self.api_token:
            return []

        params: Dict[str, Any] = {
            "text": keyword,
            "page": page,
            "per_page": per_page,
        }
        if area:
            # В реальной системе желательно маппить город в числовой код area.
            params["area"] = area

        url = f"{self.base_url}/resumes"
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url, headers=self._headers(), params=params)
                resp.raise_for_status()
        except Exception:
            return []

        data = resp.json()
        items = data.get("items") or data.get("resumes") or data.get("objects") or []

        results: List[Dict[str, Any]] = []
        for obj in items:
            if not isinstance(obj, dict):
                continue

            # Город
            city = ""
            area_obj = obj.get("area")
            if isinstance(area_obj, dict):
                city = area_obj.get("name") or area_obj.get("title") or ""
            elif isinstance(area_obj, str):
                city = area_obj

            # Навыки
            skills_list: List[str] = []
            for s in obj.get("skills") or []:
                if isinstance(s, dict):
                    name = s.get("name")
                    if name:
                        skills_list.append(name)
                elif isinstance(s, str):
                    skills_list.append(s)
            skills_text = ", ".join(skills_list)

            # Опыт
            exp_chunks: List[str] = []
            for e in obj.get("experience") or []:
                if not isinstance(e, dict):
                    continue
                position = e.get("position") or ""
                company = e.get("company") or ""
                if position or company:
                    exp_chunks.append(f"{position} — {company}")
            exp_text = "; ".join(exp_chunks)

            results.append(
                {
                    "id": obj.get("id"),
                    "name": obj.get("title") or "Кандидат hh.ru",
                    "city": city,
                    "skills": skills_text,
                    "experience": exp_text,
                    "link": obj.get("alternate_url") or "",
                    "raw": obj,
                }
            )

        return results

