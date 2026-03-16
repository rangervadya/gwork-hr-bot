from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx

from config import settings


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.deepseek_model

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_messages(
        self,
        vacancy_description: str,
        candidates_payload: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        system = (
            "Ты ИИ-HR. Тебе передан профиль вакансии и список кандидатов из разных источников. "
            "Твоя задача — для каждого кандидата выставить скор от 0 до 100 и коротко объяснить, почему такая оценка. "
            "Учитывай: соответствие роли и города, опыт, навыки, адекватность текста, мотивацию. "
            "Верни строго JSON-массив, где на каждый входной кандидат один объект с полями: "
            "`id` (как во входе), `score` (0-100 целое число), `explanation` (строка максимум 2-3 предложения). "
            "Никакого лишнего текста кроме JSON."
        )
        user = {
            "vacancy": vacancy_description,
            "candidates": candidates_payload,
        }
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ]

    def score_candidates(
        self,
        vacancy_description: str,
        candidates_payload: List[Dict[str, Any]],
        timeout: float = 30.0,
    ) -> List[Dict[str, Any]]:
        """
        Возвращает список объектов {id, score, explanation}.
        При ошибках или отсутствии ключа — возвращает пустой список.
        """
        if not self.api_key:
            return []

        url = f"{self.base_url}/chat/completions"
        messages = self._build_messages(vacancy_description, candidates_payload)

        body = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=self._headers(), json=body)
                resp.raise_for_status()
        except Exception:
            return []

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "results" in parsed and isinstance(
                parsed["results"], list
            ):
                return parsed["results"]
        except Exception:
            return []

        return []

