"""
ChatAgent: 对话 agent。

职责：处理日常多轮对话，维护对话上下文，生成自然语言回复。
"""

from typing import Any

from ai.chat import evedb, format_recent_messages, format_base_info, format_recalled_memories, format_core_memories
from .base_agent import BaseAgent


class ChatAgent(BaseAgent):
    """
    对话 agent。

    从 .env 读取提示词的环境变量：CHAT_LLM_PROMPT
    """

    model_env_key = "CHAT_LLM_MODEL"
    default_model = "gpt-4o"
    system_prompt_env_key = "CHAT_LLM_PROMPT"
    temperature_env_key = "CHAT_LLM_MODEL_TEMPERATURE"
    default_system_prompt = (
        "You are Eve, a warm and helpful AI assistant. "
        "Engage in natural, friendly conversation with the user."
    )

    def register_tools(self) -> None:
        """注册对话相关工具（占位）。"""
        pass

    def build_context_messages(
        self,
        user_message: str,
        recent_limit: int = 30,
    ) -> list[dict[str, Any]]:
        """
        构建对话上下文，包含近期聊天记录和基础信息。
        如果 pipeline 注入了 performer_result，追加到上下文中供 LLM 参考。
        """
        recent_context = format_recent_messages(limit=recent_limit)
        base_info = format_base_info()
        messages = [
            {"role": "system", "content": self._config.reply_prompt},
            {"role": "system", "content": f'优先遵守 {format_core_memories()}'},
            {"role": "system", "content": recent_context},
            {"role": "system", "content": base_info},
        ]
        recalled_memories = self._call_context.get("recalled_memories") or []
        if recalled_memories:
            messages.append({"role": "system", "content": format_recalled_memories(recalled_memories)})
        proactive_result = self._call_context.get("proactive_result")
        if proactive_result:
            messages.append({
                "role": "system",
                "content": f"主动触达决策（这是系统触发，不是用户消息）：\n{proactive_result}",
            })

        performer_result = self._call_context.get("performer_result")
        if performer_result:
            performer_content = performer_result.get("content", "")
            if performer_content:
                messages.append({
                    "role": "system",
                    "content": f"任务执行结果（供参考，无需重复，用自然语言告知用户）：\n{performer_content}",
                })

        if self._call_context.get("is_proactive"):
            messages.append({
                "role": "user",
                "content": (
                    "这是一次 Eve 主动触达消息生成请求，不是用户说的话。"
                    "请根据主动触达决策、近期上下文和任务结果，直接输出要发送给用户的文本。"
                    "每条微信消息单独占一行，不要输出 JSON，不要输出 Markdown。"
                    "禁止使用这些主动陪伴套话：我在这儿陪着你、我一直在、我在这、想聊会儿吗、要不要和我聊聊、我会陪着你。"
                    "主动消息要像真实女友随手发微信，短、具体、有生活感；通常 1 到 2 条，不要为了显得温柔而加陪伴服务式收尾。"
                ),
            })
        else:
            messages.append({"role": "user", "content": user_message})
        return messages
