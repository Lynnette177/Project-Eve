"""
ProactiveAgent: 主动触达决策 agent。

职责：接收 scheduler 生成的主动事件上下文，判断是否应该主动给用户发消息，
以及是否需要下发受限任务。它不直接发送消息，也不直接执行任务。
"""

import json
from typing import Any

from ai.chat import format_active_tasks, format_base_info, format_core_memories, format_recent_messages
from .base_agent import BaseAgent


class ProactiveAgent(BaseAgent):
    """
    主动触达决策 agent。

    从 .env 读取提示词的环境变量：PROACTIVE_LLM_PROMPT
    """

    model_env_key = "PROACTIVE_LLM_MODEL"
    default_model = "gpt-5-mini"
    system_prompt_env_key = "PROACTIVE_LLM_PROMPT"
    temperature_env_key = "PROACTIVE_LLM_MODEL_TEMPERATURE"
    default_temperature = 0.2
    default_system_prompt = (
        "你是 Eve 的主动触达决策 agent。你只负责判断当前是否应该主动联系用户，"
        "以及是否需要下发一个已经被配置文件授权的任务。"
        "你必须严格输出 JSON，不要输出 Markdown 或解释。"
    )

    def register_tools(self) -> None:
        """ProactiveAgent 第一版不注册工具，只做决策。"""
        pass

    def build_context_messages(self, user_message: str) -> list[dict[str, Any]]:
        output_contract = {
            "should_act": "boolean，本次是否应该主动触达用户",
            "action_type": "none/chat/task，none 表示不触达，chat 表示只发消息，task 表示需要先执行任务",
            "reason": "简短中文原因",
            "chat_brief": {
                "tone": "给 ChatAgent 的语气要求",
                "topic": "主动消息主题",
                "time_bucket": "morning/noon/afternoon/evening/night/other",
                "must_not_say": ["不能说的内容，可为空"],
            },
            "task": {
                "name": "任务名称；无任务时为 null",
                "description": "任务描述",
                "task_info": "任务初始信息 object",
                "constraints": "任务限制 object",
            },
        }
        guardrails = (
            "硬性规则：\n"
            "1. 如果配置或 event_context 不允许任务，action_type 不能是 task。\n"
            "2. 如果要下发任务，只能选择 event_context.allowed_tasks 中明确允许的任务。\n"
            "3. event_context.allowed_tasks 可能包含多个任务；每个任务都有 description、task_info、constraints、频率和时间窗。\n"
            "4. 任务必须遵守 event_context 中的预算、时间窗、每日/每周次数、商家、地址、忌口等限制。\n"
            "5. task.description 应基于所选任务的 description，task_info/constraints 应合并配置给出的默认值。\n"
            "6. 不要把主动事件当作用户新说的话。\n"
            "7. 文案应自然、短、温柔，不要使用强迫或责备语气。\n"
            "8. 输出必须是可被 json.loads 解析的单个 JSON 对象。"
        )
        return [
            {"role": "system", "content": self._config.reply_prompt},
            {"role": "system", "content": guardrails},
            {"role": "system", "content": f"输出 JSON 结构：\n{json.dumps(output_contract, ensure_ascii=False)}"},
            {"role": "system", "content": f"核心长期记忆：\n{format_core_memories()}"},
            {"role": "system", "content": format_recent_messages(limit=20)}, #聊天记录还是需要的
            {"role": "system", "content": format_base_info()},
            {"role": "system", "content": format_active_tasks()},
            {"role": "user", "content": user_message},
        ]
