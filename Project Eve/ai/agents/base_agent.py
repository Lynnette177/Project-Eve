"""
BaseAgent: 所有 agent 的基类，提供通用的工具注册、MCP 管理和 LLM 调用能力。
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from ai.chat import LLMConfig, evedb
from ai.mcp import MCPToolBridge, normalize_tool_result
from ai.debug_visualizer import visualize_agent_output, visualize_error

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


class BaseAgent:
    """
    所有 agent 的基类。

    子类应当重写：
        - system_prompt_env_key: 从 .env 读取提示词的环境变量名
        - default_system_prompt: 提示词默认值
        - build_context_messages(user_message): 构建发给 LLM 的消息列表

    工具注册：
        - register_tool(schema, handler): 注册本地函数工具
        - MCP 由 .env 中 MCP_SERVERS 或子类指定的 mcp_servers 管理
          MCP 连接在每次 run_async() 内临时建立，避免跨事件循环持久化。

    配置重载：
        - reload(): 重新加载 .env
    """

    # 子类应重写以下三组 env key / default
    model_env_key: str = ""
    default_model: str = "gpt-4o"
    system_prompt_env_key: str = ""
    default_system_prompt: str = "You are Eve, a helpful AI assistant."
    temperature_env_key: str = ""
    default_temperature: float = 0.2
    retryable_errors = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)

    def __init__(self):
        self._tools: list[dict[str, Any]] = []           # 本地工具 schema 列表
        self._handlers: dict[str, Callable] = {}         # 本地工具处理函数
        self._config: LLMConfig | None = None
        self._mcp_bridge: MCPToolBridge | None = None
        self._mcp_lock = asyncio.Lock()
        # Per-call extra context injected by the pipeline before run()
        self._call_context: dict[str, Any] = {}

        # 初始化时只加载配置和本地工具。
        # MCP 连接不能跨 asyncio.run() 持久化；会在每次 run_async() 内临时建立并关闭。
        self._config = self._load_config()
        self.register_tools()

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------

    def _load_config(self) -> LLMConfig:
        """重新读取 .env 并返回 LLMConfig。"""
        load_dotenv(_ENV_PATH, override=True)
        cfg = LLMConfig()
        cfg.reply_model = self._load_model()
        cfg.reply_prompt = self._load_system_prompt()
        cfg.reply_temperature = self._load_temperature()
        return cfg

    def _load_model(self) -> str:
        """从环境变量读取此 agent 的模型名，找不到则使用默认值。"""
        raw = os.getenv(self.model_env_key, "") if self.model_env_key else ""
        return raw.strip() or self.default_model

    def _load_system_prompt(self) -> str:
        """从环境变量读取此 agent 的提示词，找不到则使用默认值。"""
        raw = os.getenv(self.system_prompt_env_key, "") if self.system_prompt_env_key else ""
        return raw.strip() or self.default_system_prompt

    def _load_temperature(self) -> float:
        """从环境变量读取此 agent 的温度，找不到则使用默认值。"""
        raw = os.getenv(self.temperature_env_key, "") if self.temperature_env_key else ""
        try:
            return float(raw) if raw else self.default_temperature
        except ValueError:
            return self.default_temperature

    # ------------------------------------------------------------------
    # 工具注册（本地函数工具）
    # ------------------------------------------------------------------

    def register_tool(self, schema: dict[str, Any], handler: Callable) -> None:
        """
        注册一个本地函数工具。

        Args:
            schema: OpenAI function tool schema，格式为
                    {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
            handler: 调用该工具时执行的函数，可以是同步或异步函数。
        """
        name = schema["function"]["name"]
        self._tools.append(schema)
        self._handlers[name] = handler

    def register_tools(self) -> None:
        """
        子类重写此方法以注册自己的工具。
        在 __init__ 和 reload() 时都会被调用。
        """
        pass

    # ------------------------------------------------------------------
    # 配置重载（/reload-env 触发）
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """重新加载 .env 并重置本地工具列表。"""
        self._tools = []
        self._handlers = {}
        self._config = self._load_config()
        self.register_tools()

    # ------------------------------------------------------------------
    # 上下文构建（子类应重写）
    # ------------------------------------------------------------------

    def build_context_messages(self, user_message: str) -> list[dict[str, Any]]:
        """
        构建发给 LLM 的消息列表。

        子类应重写此方法以添加个性化上下文（记忆、任务状态等）。
        默认实现仅包含 system prompt 和用户消息。
        """
        return [
            {"role": "system", "content": self._config.reply_prompt},
            {"role": "user", "content": user_message},
        ]

    # ------------------------------------------------------------------
    # LLM 调用
    # ------------------------------------------------------------------

    def _create_completion_with_retry(self, client: OpenAI, **kwargs):
        max_attempts = int(os.getenv("LLM_MAX_RETRY_ATTEMPTS", "3") or 3)
        base_delay = float(os.getenv("LLM_RETRY_BASE_DELAY", "3") or 3)
        for attempt in range(1, max_attempts + 1):
            try:
                return client.chat.completions.create(**kwargs)
            except self.retryable_errors as exc:
                if attempt >= max_attempts:
                    raise
                retry_after = getattr(exc, "retry_after", None)
                response = getattr(exc, "response", None)
                if retry_after is None and response is not None:
                    retry_after = response.headers.get("retry-after")
                try:
                    delay = float(retry_after) if retry_after is not None else base_delay * attempt
                except (TypeError, ValueError):
                    delay = base_delay * attempt
                delay = min(delay, 120)
                print(
                    f"[{self.__class__.__name__}] retryable LLM error "
                    f"({exc.__class__.__name__}), retry {attempt}/{max_attempts - 1} in {delay:.1f}s"
                )
                time.sleep(delay)

    async def run_async(
        self,
        user_message: str,
        max_tool_rounds: int = 5,
    ) -> dict[str, Any]:
        """
        异步调用 LLM，支持工具调用循环（含本地工具和 MCP 工具）。

        Returns:
            {"content": str, "messages": list, "raw": dict}
        """
        cfg = self._config
        client = OpenAI(api_key=cfg.api_key, base_url=cfg.api_url)
        chat_history = self.build_context_messages(user_message)

        all_tools = list(self._tools)
        async with MCPToolBridge(cfg.mcp_servers) as mcp_bridge:
            all_tools.extend(mcp_bridge.tools)

            for _ in range(max_tool_rounds + 1):
                completion = self._create_completion_with_retry(
                    client,
                    model=cfg.reply_model,
                    messages=chat_history,
                    tools=all_tools or None,
                    tool_choice="auto" if all_tools else None,
                    temperature=cfg.reply_temperature,
                )
                assistant_message = completion.choices[0].message
                assistant_dict = assistant_message.model_dump(exclude_none=True)
                chat_history.append(assistant_dict)

                tool_calls = getattr(assistant_message, "tool_calls", None) or []
                if not tool_calls:
                    return {
                        "content": assistant_message.content or "",
                        "messages": chat_history,
                        "raw": completion.model_dump(),
                    }

                for tool_call in tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        arguments = json.loads(tool_call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}

                    if tool_name in self._handlers:
                        result = self._handlers[tool_name](arguments)
                        if asyncio.iscoroutine(result):
                            result = await result
                        result_text = normalize_tool_result(result)
                    elif tool_name in mcp_bridge.tool_to_session:
                        result_text = await mcp_bridge.call_tool(tool_name, arguments)
                    else:
                        result_text = f"[Error] Unknown tool: {tool_name}"

                    chat_history.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result_text,
                    })

        raise RuntimeError(f"{self.__class__.__name__}: max tool rounds exceeded")

    def run(self, user_message: str, call_context: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
        """同步调用 LLM（run_async 的同步包装）。

        Args:
            call_context: 额外数据（如 performer_result），由 pipeline 注入，
                          build_context_messages 可通过 self._call_context 读取。
                          调用结束后自动清空。
        """
        self._call_context = call_context or {}
        try:
            result = asyncio.run(self.run_async(user_message, **kwargs))
            # Visualize output
            extra_info = {
                "user_message": user_message,
                "call_context": call_context,
            } if call_context else {"user_message": user_message}
            visualize_agent_output(
                agent_name=self.__class__.__name__,
                output=result,
                extra_info=extra_info,
            )
            return result
        except Exception as e:
            visualize_error(
                agent_name=self.__class__.__name__,
                error=e,
                context={"user_message": user_message, "call_context": call_context},
            )
            raise
        finally:
            self._call_context = {}
