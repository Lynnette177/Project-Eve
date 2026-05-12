"""
Tool schemas for all agents.

Each schema follows the OpenAI function tool format:
{
    "type": "function",
    "function": {
        "name": str,
        "description": str,
        "parameters": { ... JSON Schema ... }
    }
}
"""

# ---------------------------------------------------------------------------
# MemoryAgent tools
# ---------------------------------------------------------------------------

SAVE_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_memory",
        "description": "将值得记忆的信息保存到记忆系统。针对事件记忆，包括短期与长期。",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "对需要记忆的内容的简洁描述。",
                },
                "category": {
                    "type": "string",
                    "description": "记忆类别，例如：event、preference、情感状态等。",
                },
                "importance": {
                    "type": "number",
                    "description": "重要性权重，1-5，越重要越高。默认 1。",
                },
                "emotional_weight": {
                    "type": "number",
                    "description": "情绪权重，1-5，越强烈越高。默认 1。",
                },
                "happened_at": {
                    "type": "string",
                    "description": "事件发生时间，可为空，例如 2026-04-29T12:00:00+08:00。",
                },
                "valid_until": {
                    "type": "string",
                    "description": "短期事件有效期，可为空。",
                },
            },
            "required": ["summary", "category"],
        },
    },
}

UPDATE_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_memory",
        "description": "更新一条已有记忆。用于把新信息合并进已有记忆，避免重复保存相似内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "description": "要更新的记忆 ID。必须来自上下文中的相关记忆。",
                },
                "summary": {
                    "type": "string",
                    "description": "更新后的完整记忆内容，而不是只写新增片段。",
                },
                "category": {
                    "type": "string",
                    "description": "记忆类别，可不填，默认沿用原类别。",
                },
                "importance": {
                    "type": "number",
                    "description": "重要性权重，1-5。",
                },
                "emotional_weight": {
                    "type": "number",
                    "description": "情绪权重，1-5。",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么更新这条记忆。",
                },
            },
            "required": ["memory_id", "summary"],
        },
    },
}

REINFORCE_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reinforce_memory",
        "description": "强化一条已有记忆。用于用户再次提到同一事实但没有新增信息时，增加该记忆权重而不重复保存。",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "description": "要强化的记忆 ID。必须来自 query_memory 返回的记忆。",
                },
                "reason": {
                    "type": "string",
                    "description": "为什么强化这条记忆。",
                },
            },
            "required": ["memory_id"],
        },
    },
}

UPSERT_CORE_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "upsert_core_memory",
        "description": "新增或更新核心长期记忆。用于保存用户长期画像、回复偏好、安慰偏好、称呼偏好、边界和关系风格。",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_key": {
                    "type": "string",
                    "description": "稳定唯一 key，例如 reply_style、comfort_style、nickname_preference、user_pet_preference。相似含义应使用同一个 key。",
                },
                "category": {
                    "type": "string",
                    "description": "类别，例如 reply_style、comfort_style、boundary、nickname、relationship、preference、user_profile。",
                },
                "content": {
                    "type": "string",
                    "description": "核心记忆完整内容。",
                },
                "priority": {
                    "type": "integer",
                    "description": "注入优先级 1-5，越高越应该提供给 ChatAgent。",
                },
                "confidence": {
                    "type": "number",
                    "description": "确定度 0-1。",
                },
            },
            "required": ["memory_key", "category", "content"],
        },
    },
}

UPDATE_CORE_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_core_memory",
        "description": "更新已有核心记忆。用于合并相似但更具体的新信息，避免重复核心记忆。",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer", "description": "核心记忆 ID。"},
                "content": {"type": "string", "description": "更新后的完整核心记忆内容。"},
                "category": {"type": "string", "description": "类别，可选。"},
                "memory_key": {"type": "string", "description": "稳定 key，可选。"},
                "priority": {"type": "integer", "description": "优先级 1-5，可选。"},
                "confidence": {"type": "number", "description": "确定度 0-1，可选。"},
            },
            "required": ["memory_id", "content"],
        },
    },
}

REINFORCE_CORE_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reinforce_core_memory",
        "description": "强化已有核心记忆。用于用户重复表达同一长期偏好但无新增信息时增加 mention_count。",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer", "description": "核心记忆 ID。"},
                "reason": {"type": "string", "description": "强化原因。"},
            },
            "required": ["memory_id"],
        },
    },
}

QUERY_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_memory",
        "description": (
            "查询事件记忆。用于在判断意图时回忆用户提到过的事件、偏好、地点、计划等。"
            "返回的每条记忆包含 status 字段，表示该记忆的遗忘程度：\n"
            "- active：记忆清晰，仍在有效期内，可直接使用；\n"
            "- expired：记忆开始模糊，相关性降低，参考时应有所保留；\n"
            "- archived：记忆已相当久远，只应作为远期背景参考，不宜当作近期事实；\n"
            "- forgotten：记忆几乎消失，内容可能不准确，不建议使用；\n"
            "- deleted：记忆已被清除，不应再引用。\n"
            "memory_strength 越大表示记忆越稳固、越不容易遗忘；similarity 越大表示与本次查询越相关。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要查询的自然语言内容，例如：用户昨天提到的事、用户喜欢的饮品、麦当劳订单等。",
                },
                "category": {
                    "type": "string",
                    "description": "可选记忆类别，例如 event、preference、情感状态等。",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回几条，默认 5。",
                },
                "min_similarity": {
                    "type": "number",
                    "description": "最低相似度，默认 0.25。为了查重可适当降低。",
                },
            },
            "required": ["query"],
        },
    },
}

# ---------------------------------------------------------------------------
# Task tools
# ---------------------------------------------------------------------------

CREATE_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_task",
        "description": "创建一个新任务，供 performer_agent 后续执行。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_name": {
                    "type": "string",
                    "description": "任务名称，例如：点麦当劳外卖。",
                },
                "task_description": {
                    "type": "string",
                    "description": "任务描述，说明用户希望完成什么。",
                },
                "task_info": {
                    "type": "object",
                    "description": "已知任务状态 JSON，例如餐厅、餐品、地址等。",
                    "additionalProperties": True,
                },
                "last_question": {
                    "type": "string",
                    "description": "如果任务缺少信息，下一步要问用户的问题。",
                },
            },
            "required": ["task_name", "task_description"],
        },
    },
}

GET_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_task",
        "description": "查询指定任务会话的完整状态。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "任务会话 ID。",
                }
            },
            "required": ["task_id"],
        },
    },
}

LIST_ACTIVE_TASKS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_active_tasks",
        "description": "查询当前所有未完成任务会话。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

UPDATE_TASK_INFO_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_task_info",
        "description": "合并保存用户已经补充的任务信息 task_info，例如商品、数量、规格、地址。默认不会清空已有字段。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "任务会话 ID。",
                },
                "task_info": {
                    "type": "object",
                    "description": "要合并进已有 task_info 的增量信息，例如 {\"items\": \"板烧鸡腿堡套餐\"}。不要只因为本轮没提到某字段就把它设为 null。",
                    "additionalProperties": True,
                },
                "merge": {
                    "type": "boolean",
                    "description": "是否合并到已有 task_info。默认为 true。只有明确要整体替换时才传 false。",
                },
            },
            "required": ["task_id", "task_info"],
        },
    },
}

UPDATE_TASK_STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_task_status",
        "description": "更新任务状态，可同时写入 last_question、result 或 error_message。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "任务会话 ID。",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending_info", "completed", "failed", "cancelled"],
                    "description": "任务状态。",
                },
                "last_question": {
                    "type": "string",
                    "description": "下一步要问用户的问题。",
                },
                "result": {
                    "type": "object",
                    "description": "任务结果 JSON。",
                    "additionalProperties": True,
                },
                "error_message": {
                    "type": "string",
                    "description": "失败或取消原因。",
                },
            },
            "required": ["task_id", "status"],
        },
    },
}

# ---------------------------------------------------------------------------
# Proactive tools / structured actions
# ---------------------------------------------------------------------------

PROACTIVE_DECISION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "proactive_decision",
        "schema": {
            "type": "object",
            "properties": {
                "should_act": {
                    "type": "boolean",
                    "description": "本次是否应该主动触达用户。",
                },
                "action_type": {
                    "type": "string",
                    "enum": ["none", "chat", "task"],
                    "description": "主动动作类型。none 表示不触达；chat 表示只聊天；task 表示需要先执行任务。",
                },
                "reason": {
                    "type": "string",
                    "description": "简短说明为什么做出这个决定。",
                },
                "chat_brief": {
                    "type": "object",
                    "description": "传给 ChatAgent 的主动消息生成摘要。",
                    "properties": {
                        "tone": {"type": "string"},
                        "topic": {"type": "string"},
                        "time_bucket": {"type": "string"},
                        "must_say": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "must_not_say": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "additionalProperties": True,
                },
                "task": {
                    "type": ["object", "null"],
                    "description": "需要执行任务时填写；不需要任务时为 null。",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "task_info": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                        "constraints": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                    "required": ["name", "description"],
                    "additionalProperties": True,
                },
            },
            "required": ["should_act", "action_type", "reason", "chat_brief", "task"],
            "additionalProperties": False,
        },
    },
}
