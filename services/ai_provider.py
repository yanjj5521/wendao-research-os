from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from core.database import get_setting
from core.version import APP_VERSION


class AIProviderError(RuntimeError):
    """Raised when the configured model cannot return a usable structured result."""


def _json_from_text(value: str) -> dict[str, Any]:
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderError("模型没有返回有效 JSON") from exc
    if not isinstance(payload, dict):
        raise AIProviderError("模型返回的 JSON 顶层必须是对象")
    return payload


def _openai_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content", []) if isinstance(item.get("content"), list) else []:
            if not isinstance(part, dict):
                continue
            value = part.get("text") or part.get("output_text")
            if isinstance(value, str) and value.strip():
                return value
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        value = message.get("content") if isinstance(message, dict) else ""
        if isinstance(value, str) and value.strip():
            return value
    raise AIProviderError("模型响应中没有可读取的文本")


def _endpoint(value: str) -> str:
    endpoint = value.strip()
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AIProviderError("AI 接口必须是完整的 http:// 或 https:// 地址")
    return endpoint


def provider_status() -> dict[str, Any]:
    mode = get_setting("ai_mode", "offline")
    model = get_setting("ai_model", "qwen2.5:7b")
    if mode == "ollama":
        return {
            "mode": mode,
            "model": model,
            "configured": True,
            "label": f"本地 Ollama · {model}",
            "detail": "调用失败时会自动退回离线模式。",
        }
    if mode == "openai":
        configured = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("RESEARCH_OS_AI_KEY"))
        return {
            "mode": mode,
            "model": model,
            "configured": configured,
            "label": f"外部模型 · {model}",
            "detail": "已从环境变量读取密钥。" if configured else "缺少 OPENAI_API_KEY 环境变量。",
        }
    return {
        "mode": "offline",
        "model": "",
        "configured": True,
        "label": "离线规则",
        "detail": "无需联网；评分仅作低置信度提示，最终由本人确认。",
    }


def generate_structured(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    timeout: float = 90,
) -> tuple[dict[str, Any], str]:
    """Return a schema-shaped object and a human-readable provider label.

    The caller owns semantic validation because different tasks have different
    minimum lengths and value ranges.
    """

    mode = get_setting("ai_mode", "offline")
    model = get_setting("ai_model", "qwen2.5:7b").strip() or "qwen2.5:7b"
    if mode == "offline":
        raise AIProviderError("当前使用离线模式")

    if mode == "ollama":
        endpoint = _endpoint(get_setting("ai_endpoint", "http://127.0.0.1:11434/api/generate"))
        body = {
            "model": model,
            "prompt": f"{system_prompt}\n\n{user_prompt}",
            "stream": False,
            "format": schema,
            "options": {"temperature": 0.2},
        }
        headers = {"Content-Type": "application/json", "User-Agent": f"ResearchCultivationOS/{APP_VERSION}"}
        label = f"本地 Ollama · {model}"
    elif mode == "openai":
        endpoint = _endpoint(get_setting("ai_endpoint", "https://api.openai.com/v1/responses"))
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("RESEARCH_OS_AI_KEY")
        if not api_key:
            raise AIProviderError("未设置 OPENAI_API_KEY 环境变量")
        body = {
            "model": model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": re.sub(r"[^a-zA-Z0-9_-]+", "_", schema_name)[:64] or "result",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"ResearchCultivationOS/{APP_VERSION}",
        }
        label = f"外部模型 · {model}"
    else:
        raise AIProviderError("未知 AI 模式")

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        raise AIProviderError(f"AI 接口返回 HTTP {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AIProviderError(f"无法读取 AI 接口：{exc}") from exc

    if mode == "ollama":
        raw = payload.get("response", "")
        if not isinstance(raw, str):
            raise AIProviderError("Ollama 响应缺少 response 字段")
    else:
        raw = _openai_text(payload)
    return _json_from_text(raw), label
