"""Thin, provider-agnostic wrapper around the OpenAI-compatible chat API.
Works unchanged against Ollama, DeepSeek, or Qwen (Alibaba Cloud Model
Studio) -- only .env changes, never this code. Verified live against
Ollama's /v1 endpoint with qwen3:8b (tool calling confirmed working).

Tracks cumulative input/output token usage across a run so agent/loop.py can
enforce MAX_TOKENS_PER_RUN and print a budget summary.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


class LLMClient:
    def __init__(self):
        base_url = os.environ.get("LLM_BASE_URL")
        model = os.environ.get("LLM_MODEL")
        api_key = os.environ.get("LLM_API_KEY")
        if not base_url or not model or not api_key:
            raise RuntimeError(
                "LLM_BASE_URL, LLM_MODEL and LLM_API_KEY must all be set in .env "
                "(see .env.example for Ollama/DeepSeek/Qwen templates)"
            )
        self.model = model
        self.max_tokens_per_run = int(os.environ.get("MAX_TOKENS_PER_RUN", "100000"))
        # The openai SDK's default request timeout (600s) is too short for local
        # CPU inference with a "thinking" model on long sequences -- a real
        # eval.run_eval run hit APITimeoutError on an 847aa protein and crashed
        # the whole multi-hour batch. 1800s with no auto-retry (a retry just
        # repeats the same slow call) is generous enough for this hardware.
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=1800, max_retries=0)
        self.usage = TokenUsage()

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """One chat-completion call. Returns the raw message dict (role,
        content, tool_calls) and updates self.usage. Never logs api_key --
        only messages/tools/response are ever written to a trace."""
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
        )
        if response.usage:
            self.usage.add(response.usage.prompt_tokens, response.usage.completion_tokens)

        message = response.choices[0].message
        return {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in (message.tool_calls or [])
            ],
        }

    def budget_exceeded(self) -> bool:
        return self.usage.total >= self.max_tokens_per_run
