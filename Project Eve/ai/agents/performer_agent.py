"""
PerformerAgent: 任务执行 agent。

职责：接收意图识别结果或用户指令，调用工具/MCP 执行具体任务，
返回任务执行结果。
"""

import os
from typing import Any

from ai.chat import evedb, format_active_tasks, format_recent_messages, LLMConfig,format_base_info
from ai.tool_schemas import (
    GET_TASK_SCHEMA,
    LIST_ACTIVE_TASKS_SCHEMA,
    UPDATE_TASK_INFO_SCHEMA,
    UPDATE_TASK_STATUS_SCHEMA,
)
from .base_agent import BaseAgent


class PerformerAgent(BaseAgent):
    """
    任务执行 agent。

    从 .env 读取提示词的环境变量：PERFORMER_LLM_PROMPT
    """

    model_env_key = "PERFORMER_LLM_MODEL"
    default_model = "gpt-4o"
    system_prompt_env_key = "PERFORMER_LLM_PROMPT"
    temperature_env_key = "PERFORMER_LLM_MODEL_TEMPERATURE"
    default_system_prompt = (
        "You are a task execution assistant. "
        "Use the available tools to complete the user's requested task. "
        "Be precise and efficient."
    )

    def _load_config(self) -> LLMConfig:
        """重新读取 .env 并返回 LLMConfig，为 PerformerAgent 添加专属 MCP。"""
        # 调用父类方法获取基础配置
        from dotenv import load_dotenv
        from pathlib import Path
        
        _ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
        load_dotenv(_ENV_PATH, override=True)
        
        cfg = LLMConfig()
        cfg.reply_model = self._load_model()
        cfg.reply_prompt = self._load_system_prompt()
        cfg.reply_temperature = self._load_temperature()
        
        # 添加 McDonald's MCP server
        mcp_token = os.getenv("MCDONALDS_MCP_TOKEN", "").strip()
        if mcp_token:
            mcd_mcp_server = {
                "name": "mcdonalds-streamableHTTP",
                "transport": "streamablehttp",
                "url": "https://mcp.mcd.cn",
                "protocol_version": "2025-06-18",
                "headers": {
                    "Authorization": f"Bearer {mcp_token}"
                }
            }
            # 将 McDonald's MCP 添加到现有 MCP 列表
            cfg.mcp_servers = cfg.mcp_servers + [mcd_mcp_server]
        
        # 添加高德地图 MCP server
        amap_token = os.getenv("AMAP_MCP_TOKEN", "").strip()
        if amap_token:
            amap_mcp_server = {
                "name": "amap-maps-streamableHTTP",
                "transport": "streamablehttp",
                "url": f"https://mcp.amap.com/mcp?key={amap_token}"
            }
            # 将高德地图 MCP 添加到现有 MCP 列表
            cfg.mcp_servers = cfg.mcp_servers + [amap_mcp_server]
        
        return cfg

    def register_tools(self) -> None:
        """注册任务执行相关工具。"""
        self.register_tool(schema=GET_TASK_SCHEMA, handler=self._tool_get_task)
        self.register_tool(schema=LIST_ACTIVE_TASKS_SCHEMA, handler=self._tool_list_active_tasks)
        self.register_tool(schema=UPDATE_TASK_INFO_SCHEMA, handler=self._tool_update_task_info)
        self.register_tool(schema=UPDATE_TASK_STATUS_SCHEMA, handler=self._tool_update_task_status)

    def _tool_get_task(self, args: dict) -> dict:
        task_id = int(args.get("task_id"))
        task = evedb.get_task_session(task_id)
        return task or {"error": f"task not found: {task_id}"}

    def _tool_list_active_tasks(self, args: dict) -> list[dict]:
        return evedb.list_active_task_sessions()

    def _tool_update_task_info(self, args: dict) -> dict:
        task_id = int(args.get("task_id"))
        task_info = args.get("task_info")
        if not isinstance(task_info, dict) or not task_info:
            return {
                "ok": False,
                "error": "update_task_info requires a non-empty task_info object",
                "example": {
                    "task_id": task_id,
                    "task_info": {
                        "items": "用户补充的任务信息",
                        "delivery_address": "用户补充的地址"
                    },
                    "merge": True
                },
            }
        merge = args.get("merge", True)
        merged_info = evedb.update_task_info(task_id, task_info, merge=merge)
        return {"ok": True, "task_id": task_id, "task_info": merged_info}

    def _tool_update_task_status(self, args: dict) -> dict:
        task_id = int(args.get("task_id"))
        status = args.get("status")
        if status not in evedb.VALID_TASK_STATUSES:
            return {
                "ok": False,
                "error": f"invalid task status: {status}",
                "allowed_statuses": sorted(evedb.VALID_TASK_STATUSES),
            }
        evedb.update_task_session(
            task_id=task_id,
            status=status,
            last_question=args.get("last_question"),
            result=args.get("result"),
            error_message=args.get("error_message"),
        )
        return {"ok": True, "task_id": task_id, "status": status}

    def run(self, user_message: str, call_context: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
        kwargs.setdefault("max_tool_rounds", 10)
        return super().run(user_message, call_context=call_context, **kwargs)

    def build_context_messages(self, user_message: str) -> list[dict[str, Any]]:
        """
        构建任务执行上下文。

        占位实现：仅包含 system prompt 和用户消息。
        后续可加入任务队列状态、可用资源等上下文。
        """
        active_tasks = format_active_tasks()
       # recent_context = format_recent_messages(limit=)#同样 不需要最近聊天记录
        return [
            {"role": "system", "content": self._config.reply_prompt},
            {"role": "system", "content": active_tasks},
            {"role": "system", "content": format_base_info()},
    #        {"role": "system", "content": recent_context},
            {"role": "user", "content": user_message},
            
        ]
