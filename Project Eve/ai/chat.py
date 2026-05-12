import json
import os
from pathlib import Path
from typing import Any

from database import EveDatabase
from dotenv import load_dotenv
from ai.debug_visualizer import visualize_pipeline_start, visualize_pipeline_end, visualize_step
import ai.pipeline_state as _ps
from ai.tools.tesla_api import tesla

evedb = EveDatabase()

# Load .env from project root (same directory as main.py)
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)


# ---------------------------------------------------------------------------
# LLM configuration
# ---------------------------------------------------------------------------

def get_llm_api_key() -> str:
    key = os.getenv("LLM_API_KEY", "")
    if not key:
        raise ValueError("LLM_API_KEY is not set in .env")
    return key


def get_llm_api_url() -> str:
    return os.getenv("LLM_API_URL", "https://api.openai.com/v1")


def get_mcp_servers() -> list[dict[str, str]]:
    raw = os.getenv("MCP_SERVERS", "").strip()
    if not raw:
        return []
    try:
        servers = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("MCP_SERVERS must be valid JSON") from exc
    if not isinstance(servers, list):
        raise ValueError("MCP_SERVERS must be a JSON list")
    return servers


class LLMConfig:
    """Holds all LLM configuration loaded from environment variables."""

    def __init__(self):
        self.api_key = get_llm_api_key()
        self.api_url = get_llm_api_url()
        self.reply_model: str = ""         # overridden per-agent
        self.reply_prompt: str = ""        # overridden per-agent
        self.reply_temperature: float = 0.2  # overridden per-agent
        self.mcp_servers = get_mcp_servers()

    def __repr__(self) -> str:
        return f"LLMConfig(model={self.reply_model}, api_url={self.api_url})"


# ---------------------------------------------------------------------------
# Context helpers (shared across agents)
# ---------------------------------------------------------------------------

def format_recent_messages(limit: int = 30) -> str:
    """Build a compact recent-message context block from SQLite."""
    rows = evedb.get_recent_messages(limit=limit)
    if not rows:
        return "最近聊天记录：无"

    lines = ["最近聊天记录："]
    for row in rows:
        speaker = "用户" if row["role"] == "user" else "assistant"
        message_type = row["message_type"]
        content = row.get("content") or ""
        body = content if message_type == "text" else f"[{message_type}] {content}".strip()
        lines.append(f"- {row['created_at']} {speaker}: {body}")

    return "\n".join(lines)


def format_base_info() -> str:
    lines = ["基础信息：", f"当前时间: {evedb.now()}"]
    try:
        loc = tesla.get_location(getAddress=False)
        lat = loc.get("latitude", "未知")
        lon = loc.get("longitude", "未知")
        address = loc.get("address", "未处理")
        geofence = loc.get("geofence", "未知")
        lines.append(f"用户当前位置: 纬度 {lat}, 经度 {lon}")
        if address and address not in ("未处理", "未知"):
            lines.append(f"地址: {address}")
        if geofence and geofence not in ("未知", ""):
            lines.append(f"地理围栏: {geofence}")
    except Exception:
        pass
    return "\n".join(lines) + "\n"


def format_active_tasks(limit: int = 10) -> str:
    """Build a compact active-task context block from SQLite."""
    tasks = evedb.list_active_task_sessions(limit=limit)
    if not tasks:
        return "当前 active_tasks：无"

    lines = ["当前 active_tasks："]
    for task in tasks:
        lines.append(
            "- "
            f"id={task['id']}; "
            f"status={task['status']}; "
            f"name={task['task_name']}; "
            f"description={task['task_description']}; "
            f"task_info={task.get('task_info') or {}}; "
            f"last_question={task.get('last_question') or ''}"
        )
    return "\n".join(lines)


def extract_tool_memories(agent_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract query_memory tool results from an agent run result."""
    recalled: list[dict[str, Any]] = []
    for message in agent_result.get("messages", []):
        if message.get("role") != "tool":
            continue
        content = message.get("content") or ""
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        for memory in payload.get("memories", []):
            if isinstance(memory, dict) and memory not in recalled:
                recalled.append(memory)
    return recalled


def format_recalled_memories(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return "相关记忆：无"
    lines = [
        "相关记忆：",
        "【记忆引用规则】根据记忆的 status 决定描述方式：",
        "- active：记忆清晰，可直接陈述，例如\"你之前说过…\"；",
        "- expired：记忆开始模糊，应使用模糊表述，例如\"我隐约记得…\"\"好像你之前提过…\"；",
        "- archived：记忆已很久远，必须用更不确定的语气，例如\"我有点印象你很久以前好像说过…\"\"不太确定，但感觉…\"；",
        "- forgotten：记忆几乎消失，不建议主动提及，若被追问可说\"我已经记不太清了\"；",
        "- deleted：若有则不要引用。",
    ]
    for memory in memories:
        lines.append(
            f"- id={memory.get('id')}; "
            f"category={memory.get('category')}; "
            f"status={memory.get('status')}; "
            f"content={memory.get('content')}"
        )
    return "\n".join(lines)


def format_core_memories(limit: int = 20) -> str:
    memories = evedb.list_core_memories(limit=limit)
    if not memories:
        return "核心长期记忆：无"
    lines = ["核心长期记忆（长期用户画像和回复偏好）："]
    for memory in memories:
        lines.append(
            f"- id={memory['id']}; key={memory['memory_key']}; "
            f"category={memory['category']}; priority={memory['priority']}; "
            f"content={memory['content']}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Multi-agent pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    user_message: str,
    intent_agent,
    chat_agent,
    performer_agent,
    memory_agent,
) -> dict[str, Any]:
    """
    串行执行 intent -> chat -> memory 三阶段 pipeline。

    Returns:
        {
            "intent": dict,   # IntentAgent 原始输出
            "chat":   dict,   # ChatAgent   原始输出（含 content / messages / raw）
            "memory": dict,   # MemoryAgent 原始输出
        }
    """
    visualize_pipeline_start(user_message)
    _ps.pipeline_start("chat", user_message[:60])
    
    # Step 1: 意图识别
    visualize_step("Intent Recognition", "识别用户意图：对话 or 任务")
    _ps.node_start("Intent", input_data={"user_message": user_message})
    intent_result = intent_agent.run(user_message)
    intent_json = json.loads(intent_result.get("content", "{}"))
    recalled_memories = extract_tool_memories(intent_result)

    if intent_json.get("route") == "silent" or intent_json.get("should_reply") is False:
        _ps.node_done("Intent", output_data={"messages": intent_result.get("messages", []), "content": intent_json})
        _ps.node_skip("Performer")
        _ps.node_skip("Chat")
        _ps.node_skip("Memory")
        _ps.pipeline_end()
        visualize_pipeline_end()
        return {
            "intent": intent_result,
            "chat": {"content": "", "messages": [], "raw": {}, "skipped": True},
            "memory": {"content": "", "messages": [], "raw": {}, "skipped": True},
            "recalled_memories": recalled_memories,
            "should_reply": False,
        }

    performer_result = None
    if intent_json["route"] == "task":
        _ps.node_done("Intent", "Performer", output_data={"messages": intent_result.get("messages", []), "content": intent_json})
        visualize_step("Task Performer", "执行任务相关操作")
        active_task_id = intent_json.get("active_task_id")
        if active_task_id is None:
            task = intent_json["task"]
            active_task_id = evedb.create_task_session(
                task_name=task["name"],
                task_description=task["description"],
                task_info={},
                status="pending_info",
            )
        performer_input = {
            "user_message": user_message,
            "intent": intent_json,
            "active_task_id": active_task_id,
            "recalled_memories": recalled_memories,
        }
        _ps.node_start("Performer", input_data=performer_input)
        performer_result = performer_agent.run(
            json.dumps(performer_input, ensure_ascii=False)
        )
        _ps.node_done("Performer", "Chat", output_data={"messages": performer_result.get("messages", []), "content": performer_result.get("content")})
    else:
        _ps.node_done("Intent", "Chat", output_data={"messages": intent_result.get("messages", []), "content": intent_json})
        _ps.node_skip("Performer")
    
    # Step 2: 对话生成
    visualize_step("Chat Generation", "生成回复消息")
    chat_input = {"user_message": user_message, "performer_result": bool(performer_result), "recalled_memories_count": len(recalled_memories)}
    _ps.node_start("Chat", input_data=chat_input)
    chat_result = chat_agent.run(
        user_message,
        call_context={"performer_result": performer_result, "recalled_memories": recalled_memories},
    )
    _ps.node_done("Chat", "Memory", output_data={"messages": chat_result.get("messages", []), "content": chat_result.get("content")})
    
    # Step 3: 记忆判断（以本轮对话为输入）
    visualize_step("Memory Processing", "处理记忆存储")
    memory_input = f"用户说：{user_message}"
    _ps.node_start("Memory", input_data={"memory_input": memory_input})
    try:
        memory_result = memory_agent.run(memory_input)
    except Exception as exc:
        print(f"[run_pipeline] MemoryAgent failed, continue without memory update: {exc}")
        memory_result = {
            "content": json.dumps(
                {"should_save": False, "reason": f"MemoryAgent failed: {exc}"},
                ensure_ascii=False,
            ),
            "messages": [],
            "raw": {},
            "error": str(exc),
        }
    _ps.node_done("Memory", output_data={"messages": memory_result.get("messages", []), "content": memory_result.get("content")})
    
    visualize_pipeline_end()
    _ps.pipeline_end()

    return {
        "intent": intent_result,
        "chat": chat_result,
        "memory": memory_result,
        "recalled_memories": recalled_memories,
    }


def _safe_json_loads(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except json.JSONDecodeError:
        return default


def is_proactive_task_allowed(task: dict[str, Any] | None, event_context: dict[str, Any]) -> tuple[bool, str]:
    if not task:
        return False, "missing task"

    task_name = task.get("name")
    allowed_tasks = event_context.get("allowed_tasks") or {}
    task_config = allowed_tasks.get(task_name)
    if not task_config or not task_config.get("enabled", False):
        return False, f"proactive task is not enabled: {task_name}"

    constraints = task.get("constraints") or {}
    configured_constraints = task_config.get("constraints") or {}
    budget_max = configured_constraints.get("budget_max", task_config.get("budget_max"))
    requested_budget = constraints.get("budget_max") or (task.get("task_info") or {}).get("budget_max")
    if budget_max is not None and requested_budget is not None and float(requested_budget) > float(budget_max):
        return False, f"requested budget exceeds configured budget_max: {requested_budget} > {budget_max}"

    return True, "allowed"


def get_proactive_task_config(task: dict[str, Any] | None, event_context: dict[str, Any]) -> dict[str, Any]:
    if not task:
        return {}
    return (event_context.get("allowed_tasks") or {}).get(task.get("name"), {}) or {}


def run_proactive_pipeline(
    event_context: dict[str, Any],
    proactive_agent,
    chat_agent,
    performer_agent,
) -> dict[str, Any]:
    """
    串行执行 proactive -> performer? -> chat 主动触达 pipeline。

    ProactiveAgent 只做决策；是否真的执行任务由 Python 层根据 event_context
    和配置硬校验。主动链路不调用 MemoryAgent。
    """
    event_input = json.dumps(event_context, ensure_ascii=False)
    visualize_pipeline_start(f"[proactive] {event_context.get('event_type', 'scheduled_check')}")
    _ps.pipeline_start("proactive", event_context.get("event_type", "proactive"))

    visualize_step("Proactive Decision", "判断是否主动触达以及是否下发任务")
    _ps.node_start("Proactive", input_data={"event_context": event_context})
    proactive_result = proactive_agent.run(event_input)
    proactive_json = _safe_json_loads(proactive_result.get("content", ""), default={})
    if not isinstance(proactive_json, dict):
        proactive_json = {}

    should_act = bool(proactive_json.get("should_act"))
    action_type = proactive_json.get("action_type", "none")
    if not should_act or action_type == "none":
        _ps.node_done("Proactive", output_data={"messages": proactive_result.get("messages", []), "content": proactive_json})
        _ps.node_skip("Performer")
        _ps.node_skip("Chat")
        event_id = evedb.add_proactive_event(
            event_type=event_context.get("event_type", "scheduled_check"),
            status="skipped",
            action_type="none",
            reason=proactive_json.get("reason", "ProactiveAgent decided not to act."),
            decision=proactive_json,
            metadata=event_context,
            scheduled_at=event_context.get("scheduled_at"),
        )
        visualize_pipeline_end()
        _ps.pipeline_end()
        return {
            "event_id": event_id,
            "proactive": proactive_result,
            "proactive_decision": proactive_json,
            "performer": None,
            "chat": None,
            "should_send": False,
        }

    event_id = evedb.add_proactive_event(
        event_type=event_context.get("event_type", "scheduled_check"),
        status="planned",
        action_type=action_type,
        reason=proactive_json.get("reason"),
        decision=proactive_json,
        metadata=event_context,
        scheduled_at=event_context.get("scheduled_at"),
    )

    performer_result = None
    active_task_id = None
    if action_type == "task":
        task = proactive_json.get("task")
        allowed, reason = is_proactive_task_allowed(task, event_context)
        if not allowed:
            _ps.node_done("Proactive", output_data={"messages": proactive_result.get("messages", []), "content": proactive_json})
            _ps.node_skip("Performer")
            _ps.node_skip("Chat")
            evedb.update_proactive_event(event_id, status="failed", reason=reason)
            visualize_pipeline_end()
            _ps.pipeline_end(error=reason)
            return {
                "event_id": event_id,
                "proactive": proactive_result,
                "proactive_decision": proactive_json,
                "performer": None,
                "chat": None,
                "should_send": False,
                "error": reason,
            }

        task_config = get_proactive_task_config(task, event_context)
        _ps.node_done("Proactive", "Performer", output_data={"messages": proactive_result.get("messages", []), "content": proactive_json})
        visualize_step("Task Performer", "执行主动触达任务")
        performer_input_data = {
            "origin": "proactive",
            "event_context": event_context,
            "proactive_decision": proactive_json,
            "active_task_id": None,
            "task": task,
        }
        _ps.node_start("Performer", input_data=performer_input_data)
        active_task_id = evedb.create_task_session(
            task_name=task["name"],
            task_description=task["description"],
            task_info={
                "origin": "proactive",
                "proactive_event_id": event_id,
                "configured_task": {
                    "description": task_config.get("description"),
                    "task_info": task_config.get("task_info") or {},
                    "constraints": task_config.get("constraints") or {},
                },
                **(task.get("task_info") or {}),
                "constraints": {
                    **(task_config.get("constraints") or {}),
                    **(task.get("constraints") or {}),
                },
            },
            status="pending_info",
        )
        evedb.update_proactive_event(event_id, status="task_started", task_id=active_task_id)
        performer_input = {
            "origin": "proactive",
            "event_context": event_context,
            "proactive_decision": proactive_json,
            "active_task_id": active_task_id,
            "task": task,
        }
        performer_result = performer_agent.run(json.dumps(performer_input, ensure_ascii=False))
        _ps.node_done("Performer", "Chat", output_data={"messages": performer_result.get("messages", []), "content": performer_result.get("content")})
    else:
        _ps.node_done("Proactive", "Chat", output_data={"messages": proactive_result.get("messages", []), "content": proactive_json})
        _ps.node_skip("Performer")

    visualize_step("Chat Generation", "生成主动触达消息")
    _ps.node_start("Chat", input_data={"trigger": "[proactive event]", "proactive_decision": proactive_json, "performer_done": bool(performer_result)})
    chat_result = chat_agent.run(
        "[proactive event]",
        call_context={
            "is_proactive": True,
            "proactive_result": proactive_json,
            "performer_result": performer_result,
        },
    )
    _ps.node_done("Chat", output_data={"messages": chat_result.get("messages", []), "content": chat_result.get("content")})

    evedb.update_proactive_event(
        event_id,
        action_type=action_type,
        decision=proactive_json,
        task_id=active_task_id,
    )
    visualize_pipeline_end()
    _ps.pipeline_end()

    return {
        "event_id": event_id,
        "proactive": proactive_result,
        "proactive_decision": proactive_json,
        "performer": performer_result,
        "chat": chat_result,
        "should_send": True,
    }
