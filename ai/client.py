"""LLM 客户端：OpenAI 兼容接口，通过 .env 配置。

支持 DeepSeek / Kimi / 通义 / GLM / OpenAI 等任何兼容服务。
"""
import json
import os

from loguru import logger
from openai import OpenAI

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("AI_API_KEY", "")
        if "填" in api_key or not api_key:
            raise RuntimeError("未配置 AI_API_KEY —— 请复制 .env.example 为 .env 并填写")
        _client = OpenAI(
            base_url=os.getenv("AI_BASE_URL"),
            api_key=api_key,
        )
    return _client


def _chat(messages: list, temperature: float, retries: int) -> str:
    client = get_client()
    model = os.getenv("AI_MODEL", "deepseek-chat")
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature)
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            last_err = e
            logger.warning(f"AI 调用失败 ({attempt + 1}/{retries + 1}): {e}")
    raise RuntimeError(f"AI 调用最终失败: {last_err}")


def chat_json(system: str, user: str, temperature: float = 0.3, retries: int = 2) -> dict:
    """请求模型并解析 JSON 输出（自动剥掉 ```json 包裹，带重试）。"""
    content = _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        temperature, retries)
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # 最后抢救一次：截取第一个 { 到最后一个 }
        s, e = content.find("{"), content.rfind("}")
        if s >= 0 and e > s:
            return json.loads(content[s:e + 1])
        raise


def chat_text(system: str, user: str, temperature: float = 0.7, retries: int = 2) -> str:
    """请求模型返回纯文本。"""
    return _chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        temperature, retries)
