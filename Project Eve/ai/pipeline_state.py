"""
pipeline_state.py — 全局 pipeline 运行状态，供 /api/pipeline-status 接口读取。

两种 pipeline：
  - "chat"       : Intent → (Performer?) → Chat → Memory
  - "proactive"  : Proactive → (Performer?) → Chat
"""

import threading
import time
from typing import Any

_lock = threading.Lock()

# 节点定义（顺序即默认排列顺序）
CHAT_NODES = ["Intent", "Performer", "Chat", "Memory"]
PROACTIVE_NODES = ["Proactive", "Performer", "Chat"]

_DEFAULT: dict[str, Any] = {
    "pipeline_type": None,       # "chat" | "proactive" | None
    "running": False,
    "active_node": None,         # 正在执行的节点名
    "completed_nodes": [],       # 已完成的节点名列表
    "skipped_nodes": [],         # 跳过的节点（如没任务时跳过 Performer）
    "edges": [],                 # 数据流边: [{"from": "Intent", "to": "Performer"}]
    "started_at": None,          # 开始时间戳
    "finished_at": None,         # 结束时间戳
    "trigger": "",               # 触发描述（用户消息前几个字 或 proactive 类型）
    "error": None,
}

_state: dict[str, Any] = dict(_DEFAULT)

# Per-node input/output data
_node_data: dict[str, dict[str, Any]] = {}  # { "NodeId": {"input": ..., "output": ...} }


def _snapshot() -> dict[str, Any]:
    with _lock:
        return {**dict(_state), "node_data": {k: dict(v) for k, v in _node_data.items()}}


def get_state() -> dict[str, Any]:
    return _snapshot()


def pipeline_start(pipeline_type: str, trigger: str) -> None:
    with _lock:
        _node_data.clear()
        _state.update(
            pipeline_type=pipeline_type,
            running=True,
            active_node=None,
            completed_nodes=[],
            skipped_nodes=[],
            edges=[],
            started_at=time.time(),
            finished_at=None,
            trigger=trigger[:60],
            error=None,
        )


def node_start(node: str, input_data: Any = None) -> None:
    with _lock:
        _state["active_node"] = node
        _node_data[node] = {"input": input_data, "output": None}


def node_done(node: str, next_node: str | None = None, output_data: Any = None) -> None:
    with _lock:
        completed = _state.get("completed_nodes") or []
        if node not in completed:
            completed.append(node)
        _state["completed_nodes"] = completed
        _state["active_node"] = None
        if node in _node_data:
            _node_data[node]["output"] = output_data
        else:
            _node_data[node] = {"input": None, "output": output_data}
        if next_node:
            edges = _state.get("edges") or []
            edge = {"from": node, "to": next_node}
            if edge not in edges:
                edges.append(edge)
            _state["edges"] = edges


def node_skip(node: str) -> None:
    with _lock:
        skipped = _state.get("skipped_nodes") or []
        if node not in skipped:
            skipped.append(node)
        _state["skipped_nodes"] = skipped


def pipeline_end(error: str | None = None) -> None:
    with _lock:
        _state["running"] = False
        _state["active_node"] = None
        _state["finished_at"] = time.time()
        _state["error"] = error
