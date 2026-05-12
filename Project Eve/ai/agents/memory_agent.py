"""
MemoryAgent: 记忆判断 agent。

职责：分析对话内容，判断是否包含值得长期记忆的信息，
并将重要信息提取后存入记忆系统。
"""

import json
from typing import Any

from ai.chat import evedb, format_core_memories, format_recent_messages
from ai.tool_schemas import (
    QUERY_MEMORY_SCHEMA,
    REINFORCE_CORE_MEMORY_SCHEMA,
    REINFORCE_MEMORY_SCHEMA,
    SAVE_MEMORY_SCHEMA,
    UPDATE_CORE_MEMORY_SCHEMA,
    UPDATE_MEMORY_SCHEMA,
    UPSERT_CORE_MEMORY_SCHEMA,
)
from .base_agent import BaseAgent


class MemoryAgent(BaseAgent):
    """
    记忆判断 agent。

    从 .env 读取提示词的环境变量：MEMORY_LLM_PROMPT
    """

    model_env_key = "MEMORY_LLM_MODEL"
    default_model = "gpt-4o"
    system_prompt_env_key = "MEMORY_LLM_PROMPT"
    temperature_env_key = "MEMORY_LLM_MODEL_TEMPERATURE"
    default_system_prompt = (
        "You are a memory evaluation assistant. "
        "Analyze the conversation and determine whether it contains information "
        "worth storing as long-term memory. "
        "If yes, extract and summarize the key information in a structured format."
    )

    def register_tools(self) -> None:
        """注册记忆相关工具。"""
        self.register_tool(schema=QUERY_MEMORY_SCHEMA, handler=self._tool_query_memory)
        self.register_tool(schema=SAVE_MEMORY_SCHEMA, handler=self._tool_save_memory)
        self.register_tool(schema=UPDATE_MEMORY_SCHEMA, handler=self._tool_update_memory)
        self.register_tool(schema=REINFORCE_MEMORY_SCHEMA, handler=self._tool_reinforce_memory)
        self.register_tool(schema=UPSERT_CORE_MEMORY_SCHEMA, handler=self._tool_upsert_core_memory)
        self.register_tool(schema=UPDATE_CORE_MEMORY_SCHEMA, handler=self._tool_update_core_memory)
        self.register_tool(schema=REINFORCE_CORE_MEMORY_SCHEMA, handler=self._tool_reinforce_core_memory)

    # ------------------------------------------------------------------
    # 工具处理函数
    # ------------------------------------------------------------------

    def _tool_query_memory(self, args: dict) -> dict:
        """让 MemoryAgent 主动查询已有记忆，以便判断更新还是新增。"""
        query = args.get("query", "")
        if not query:
            return {"memories": [], "note": "query 为空"}
        memories = evedb.search_event_memories(
            query=query,
            category=args.get("category"),
            limit=int(args.get("limit", 8)),
            min_similarity=float(args.get("min_similarity", 0.25)),
        )
        return {
            "note": "similarity 越大表示越相关；memory_strength 越大表示越稳定、越不容易遗忘。",
            "memories": [
                {
                    "id": memory["id"],
                    "content": memory["content"],
                    "category": memory["category"],
                    "status": memory["status"],
                    "memory_strength": round(float(memory["forget_weight"]), 2),
                    "similarity": round(float(memory.get("similarity", 0)), 2),
                }
                for memory in memories
            ],
        }

    def _tool_save_memory(self, args: dict) -> str:
        """将事件记忆存入 SQLite，重复记忆会被加权强化。"""
        summary = args.get("summary", "")
        category = args.get("category", "general")
        if not summary:
            return "未记录：summary 为空"

        result = evedb.save_event_memory_dedup(
            content=summary,
            category=category,
            happened_at=args.get("happened_at"),
            valid_until=args.get("valid_until"),
            importance=float(args.get("importance", 1.0)),
            emotional_weight=float(args.get("emotional_weight", 1.0)),
        )
        memory = result.get("memory") or {}
        action = result.get("action")
        print(f"[MemoryAgent] save_memory {action}: [{category}] {summary}")
        return json.dumps(
            {
                "ok": True,
                "action": action,
                "memory_id": memory.get("id"),
                "forget_weight": memory.get("forget_weight"),
                "mention_count": memory.get("mention_count"),
                "matched_similarity": result.get("matched_similarity"),
            },
            ensure_ascii=False,
        )

    def _tool_update_memory(self, args: dict) -> str:
        """更新已有记忆并强化权重，避免相似记忆重复保存。"""
        memory_id = int(args.get("memory_id"))
        summary = args.get("summary", "")
        if not summary:
            return "未更新：summary 为空"

        current = evedb.get_event_memory(memory_id)
        if not current:
            return json.dumps({"ok": False, "error": f"memory not found: {memory_id}"}, ensure_ascii=False)

        evedb.update_event_memory(
            memory_id=memory_id,
            content=summary,
            category=args.get("category") or current.get("category"),
            importance=float(args.get("importance", current.get("importance", 1.0))),
            emotional_weight=float(args.get("emotional_weight", current.get("emotional_weight", 1.0))),
        )
        updated = evedb.reinforce_event_memory(memory_id, content=summary)
        print(f"[MemoryAgent] update_memory: id={memory_id} {summary}")
        return json.dumps(
            {
                "ok": True,
                "action": "updated",
                "memory_id": memory_id,
                "forget_weight": updated.get("forget_weight"),
                "mention_count": updated.get("mention_count"),
            },
            ensure_ascii=False,
        )

    def _tool_reinforce_memory(self, args: dict) -> str:
        """用户重复提到已有记忆时，只强化权重，不新增、不改写。"""
        memory_id = int(args.get("memory_id"))
        current = evedb.get_event_memory(memory_id)
        if not current:
            return json.dumps({"ok": False, "error": f"memory not found: {memory_id}"}, ensure_ascii=False)
        reinforced = evedb.reinforce_event_memory(memory_id)
        print(f"[MemoryAgent] reinforce_memory: id={memory_id} reason={args.get('reason', '')}")
        return json.dumps(
            {
                "ok": True,
                "action": "reinforced",
                "memory_id": memory_id,
                "forget_weight": reinforced.get("forget_weight"),
                "mention_count": reinforced.get("mention_count"),
            },
            ensure_ascii=False,
        )

    def _tool_upsert_core_memory(self, args: dict) -> str:
        result = evedb.upsert_core_memory(
            memory_key=args.get("memory_key", "general"),
            category=args.get("category", "general"),
            content=args.get("content", ""),
            priority=int(args.get("priority", 3)),
            confidence=float(args.get("confidence", 1.0)),
        )
        memory = result.get("memory") or {}
        return json.dumps(
            {"ok": True, "action": result.get("action"), "memory_id": memory.get("id")},
            ensure_ascii=False,
        )

    def _tool_update_core_memory(self, args: dict) -> str:
        memory = evedb.update_core_memory(
            memory_id=int(args.get("memory_id")),
            content=args.get("content"),
            category=args.get("category"),
            memory_key=args.get("memory_key"),
            priority=args.get("priority"),
            confidence=args.get("confidence"),
        )
        return json.dumps({"ok": True, "action": "updated", "memory_id": memory.get("id")}, ensure_ascii=False)

    def _tool_reinforce_core_memory(self, args: dict) -> str:
        memory = evedb.reinforce_core_memory(int(args.get("memory_id")))
        return json.dumps(
            {
                "ok": True,
                "action": "reinforced",
                "memory_id": memory.get("id"),
                "mention_count": memory.get("mention_count"),
            },
            ensure_ascii=False,
        )

    def build_context_messages(
        self,
        user_message: str,
        recent_limit: int = 10,
    ) -> list[dict[str, Any]]:
        """不自动注入记忆，由 MemoryAgent 自己按关键词调用 query_memory。"""
        recent_messages = format_recent_messages(10)
        return [
            {"role": "system", "content": self._config.reply_prompt},
            {"role": "system", "content": f'已有的核心记忆，切勿重复存储：{format_core_memories()}'},
            {"role": "system", "content": f"最近的聊天记录（仅供参考，不要重复处理旧内容）：\n{recent_messages}"},
            {
                "role": "system",
                "content": "如需判断是否与已有记忆重复或应合并，请先用 query_memory 主动查询关键词、同义词、上位词和相关概念。例如：小狗/狗/犬类/宠物。",
            },
            {"role": "user", "content": user_message},
        ]
