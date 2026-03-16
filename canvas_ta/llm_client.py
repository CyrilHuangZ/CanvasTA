from __future__ import annotations

from typing import Any

import requests

from .config import Settings


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        if not self.settings.llm_api_key:
            raise ValueError("缺少 LLM_API_KEY，请在环境变量中配置。")
        if self.settings.is_azure_openai:
            return {
                "api-key": self.settings.llm_api_key,
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }

    def _request_url(self, model: str) -> str:
        if self.settings.is_azure_openai:
            endpoint = self.settings.azure_openai_endpoint.rstrip("/")
            if not endpoint:
                raise ValueError("使用 Azure OpenAI 时需要配置 AZURE_OPENAI_ENDPOINT。")
            api_version = self.settings.azure_openai_api_version
            return (
                f"{endpoint}/openai/deployments/{model}/chat/completions"
                f"?api-version={api_version}"
            )
        return self.settings.resolved_llm_api_url

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 4000,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if not self.settings.is_azure_openai:
            payload["model"] = model
        if response_format:
            payload["response_format"] = response_format

        response = requests.post(
            self._request_url(model),
            headers=self._headers(),
            json=payload,
            timeout=self.settings.request_timeout,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def message_text(api_result: dict[str, Any]) -> str:
        choices = api_result.get("choices", [])
        if not choices:
            raise ValueError(f"模型返回中没有 choices: {api_result}")

        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [item.get("text", "") for item in content if item.get("type") == "text"]
            return "\n".join(parts).strip()
        raise ValueError(f"无法解析模型输出内容: {content}")
