"""
IntentAgent: 意图识别 agent。

职责：分析用户消息，判断用户意图，输出结构化意图标签，
供后续 agent 路由决策使用。
"""

from typing import Any

from ai.chat import evedb, format_active_tasks
from ai.tool_schemas import QUERY_MEMORY_SCHEMA
from .base_agent import BaseAgent


class IntentAgent(BaseAgent):
    """
    意图识别 agent。

    从 .env 读取提示词的环境变量：INTENT_LLM_PROMPT
    """

    model_env_key = "INTENT_LLM_MODEL"
    default_model = "gpt-4o"
    system_prompt_env_key = "INTENT_LLM_PROMPT"
    temperature_env_key = "INTENT_LLM_MODEL_TEMPERATURE"
    default_system_prompt = (
        "You are an intent recognition assistant. "
        "Analyze the user's message and return a structured JSON with the detected intent."
    )

    def register_tools(self) -> None:
        """注册意图识别相关工具。"""
        self.register_tool(schema=QUERY_MEMORY_SCHEMA, handler=self._tool_query_memory)

    def _tool_query_memory(self, args: dict) -> dict:
        query = args.get("query", "")
        if not query:
            return {"memories": []}
        memories = evedb.search_event_memories(
            query=query,
            category=args.get("category"),
            limit=int(args.get("limit", 5)),
            min_similarity=0.35,
        )
        for memory in memories:
            evedb.mark_event_memory_recalled(memory["id"], weight_bonus=0.2)
        return {
            "note": "memory_strength 越大，表示记忆越稳固、越不容易遗忘；similarity 越大，表示和本次查询越相关",
            "memories": [
                {
                    "id": memory["id"],
                    "content": memory["content"],
                    "category": memory["category"],
                    "status": memory["status"],
                    "memory_strength": round(memory["forget_weight"], 2),
                    "similarity": round(memory.get("similarity", 0), 2),
                }
                for memory in memories
                if memory["status"] != "deleted"
            ]
        }

    def build_context_messages(self, user_message: str) -> list[dict[str, Any]]:
        """
        构建意图识别上下文。

        占位实现：仅包含 system prompt 和用户消息。
        后续可加入近期消息摘要、用户画像等辅助上下文。
        """
        active_tasks = format_active_tasks()
        return [
            {"role": "system", "content": self._config.reply_prompt},
            {"role": "system", "content": active_tasks},
            {"role": "user", "content": user_message},
        ]
