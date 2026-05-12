"""Scheduler for Eve's proactive message pipeline."""

import json
import random
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ai.chat import evedb, run_proactive_pipeline
from ai.chat_output import normalize_chat_output

CHINA_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "proactive_config.json"

DEFAULT_CONFIG: dict[str, Any] = {
}


def now_china() -> datetime:
    return datetime.now(CHINA_TZ)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def time_bucket(current: datetime | None = None) -> str:
    hour = (current or now_china()).hour
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 14:
        return "noon"
    if 14 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "evening"
    if 23 <= hour or hour < 5:
        return "night"
    return "other"


def minutes_since(value: str | None, current: datetime | None = None) -> float | None:
    parsed = parse_iso(value)
    if not parsed:
        return None
    return ((current or now_china()) - parsed).total_seconds() / 60


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
        return dict(default)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(default)
    if not isinstance(loaded, dict):
        return dict(default)
    return deep_merge(default, loaded)


class ProactiveScheduler:
    """Background scheduler that triggers the proactive pipeline."""

    def __init__(
        self,
        proactive_agent,
        chat_agent,
        performer_agent,
        send_messages: Callable[[list[dict[str, Any]], int], None],
        config_path: str | Path = DEFAULT_CONFIG_PATH,
    ):
        self.proactive_agent = proactive_agent
        self.chat_agent = chat_agent
        self.performer_agent = performer_agent
        self.send_messages = send_messages
        self.config_path = Path(config_path)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.config = self.load_config()

    def load_config(self) -> dict[str, Any]:
        self.config = load_json(self.config_path, DEFAULT_CONFIG)
        return self.config

    def reload(self) -> None:
        with self._lock:
            self.load_config()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def on_user_message(self) -> None:
        with self._lock:
            self.load_config()
            self._set_next_proactive_at(self._random_next_time(now_china()))

    def _get_next_proactive_at(self) -> datetime | None:
        return parse_iso(evedb.get_proactive_setting("next_proactive_at"))

    def _set_next_proactive_at(self, value: datetime) -> None:
        evedb.set_proactive_setting("next_proactive_at", value.isoformat())

    def _random_next_time(self, current: datetime) -> datetime:
        bucket = time_bucket(current)
        ranges = self.config.get("random_delay_minutes", {})
        min_delay, max_delay = ranges.get(bucket) or ranges.get("other") or [60, 150]
        return current + timedelta(minutes=random.randint(int(min_delay), int(max_delay)))

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
                #print("[ProactiveScheduler] ticked")
            except Exception as exc:
                print(f"[ProactiveScheduler] tick failed: {exc}")
            interval = int(self.config.get("check_interval_seconds", 60))
            self._stop_event.wait(max(5, interval))

    def tick(self) -> None:
        with self._lock:
            self.load_config()
            if not self.config.get("enabled", True):
                return
            current = now_china()

            # 固定时间任务检查（独立于随机调度）
            fixed_tasks = self._get_due_fixed_tasks(current)
            if fixed_tasks:
                event_context = self._build_event_context(current, task_whitelist=fixed_tasks)
                event_context["event_type"] = "fixed_time_task_check"

        if fixed_tasks:
            print(f"[ProactiveScheduler] triggering fixed-time task check: {fixed_tasks}")
            pipeline = run_proactive_pipeline(
                event_context=event_context,
                proactive_agent=self.proactive_agent,
                chat_agent=self.chat_agent,
                performer_agent=self.performer_agent,
            )
            self._handle_pipeline_result(pipeline)
            return

        with self._lock:
            due, reason = self._is_due(current)
            if not due:
                return
            event_context = self._build_event_context(current)
            self._set_next_proactive_at(self._random_next_time(current))

        print(f"[ProactiveScheduler] triggering proactive pipeline: {reason}")
        pipeline = run_proactive_pipeline(
            event_context=event_context,
            proactive_agent=self.proactive_agent,
            chat_agent=self.chat_agent,
            performer_agent=self.performer_agent,
        )
        self._handle_pipeline_result(pipeline)

    def status_snapshot(self) -> dict[str, Any]:
        with self._lock:
            self.load_config()
            current = now_china()
            due, reason = self._is_due(current)
            return {
                "enabled": self.config.get("enabled", True),
                "current_time": current.isoformat(),
                "next_proactive_at": evedb.get_proactive_setting("next_proactive_at"),
                "last_user_message_at": evedb.get_latest_message_at(role="user"),
                "last_proactive_sent_at": evedb.get_latest_proactive_event_at(status="sent"),
                "minutes_since_user_message": minutes_since(evedb.get_latest_message_at(role="user"), current),
                "minutes_since_last_proactive": minutes_since(evedb.get_latest_proactive_event_at(status="sent"), current),
                "daily_sent_count": self._today_sent_count(current),
                "daily_message_limit": self.config.get("daily_message_limit"),
                "is_due": due,
                "due_reason": reason,
                "allowed_tasks": self._build_event_context(current)["allowed_tasks"],
            }

    def force_run(
        self,
        send: bool = False,
        event_context_patch: dict[str, Any] | None = None,
        force_task: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self.load_config()
            current = now_china()
            event_context = self._build_event_context(current)
            if force_task:
                task_config = self.config.get("allowed_tasks", {}).get(force_task)
                if not task_config:
                    raise ValueError(f"Unknown proactive task: {force_task}")
                event_context["allowed_tasks"] = {
                    force_task: {
                        **task_config,
                        "enabled": True,
                        "name": force_task,
                        "daily_used": self._task_day_count(force_task, current),
                        "weekly_used": self._task_week_count(force_task, current),
                    }
                }
                event_context["task_block_reason"] = None
                event_context["force_task"] = force_task
                event_context["test_instruction"] = f"这是手动测试，请优先选择任务 {force_task}，不要因为真实时间窗或概率跳过。"
            if event_context_patch:
                event_context = deep_merge(event_context, event_context_patch)
            event_context["event_type"] = "manual_proactive_test"
            event_context["manual_test"] = True

        pipeline = run_proactive_pipeline(
            event_context=event_context,
            proactive_agent=self.proactive_agent,
            chat_agent=self.chat_agent,
            performer_agent=self.performer_agent,
        )
        if send:
            self._handle_pipeline_result(pipeline)
        return pipeline

    def _is_due(self, current: datetime) -> tuple[bool, str]:
        next_at = self._get_next_proactive_at()
        if not next_at:
            self._set_next_proactive_at(self._random_next_time(current))
            return False, "next proactive time initialized"
        if current < next_at:
            return False, "not due yet"
        if self._in_quiet_hours(current):
            self._set_next_proactive_at(self._random_next_time(current))
            return False, "quiet hours"
        since_user = minutes_since(evedb.get_latest_message_at(role="user"), current)
        if since_user is None:
            return False, "no user message yet"
        min_after_user = float(self.config.get("min_minutes_after_user_message", 30))
        if since_user < min_after_user:
            return False, "too soon after user message"
        since_sent = minutes_since(evedb.get_latest_proactive_event_at(status="sent"), current)
        min_between = float(self.config.get("min_minutes_between_proactive_messages", 120))
        if since_sent is not None and since_sent < min_between:
            return False, "too soon after proactive message"
        if self._today_sent_count(current) >= int(self.config.get("daily_message_limit", 2)):
            return False, "daily proactive limit reached"
        return True, "due"

    def _in_quiet_hours(self, current: datetime) -> bool:
        quiet = self.config.get("quiet_hours") or {}
        start = quiet.get("start")
        end = quiet.get("end")
        if not start or not end:
            return False
        current_minutes = current.hour * 60 + current.minute
        start_minutes = self._hhmm_to_minutes(start)
        end_minutes = self._hhmm_to_minutes(end)
        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes < end_minutes
        return current_minutes >= start_minutes or current_minutes < end_minutes

    @staticmethod
    def _hhmm_to_minutes(value: str) -> int:
        hour, minute = value.split(":", 1)
        return int(hour) * 60 + int(minute)

    def _day_start(self, current: datetime) -> str:
        return current.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    def _week_start(self, current: datetime) -> str:
        start = current - timedelta(days=current.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    def _today_sent_count(self, current: datetime) -> int:
        return evedb.count_proactive_events_since(self._day_start(current), status="sent")

    def _task_week_count(self, task_name: str, current: datetime) -> int:
        return evedb.count_proactive_events_since(
            self._week_start(current),
            action_type="task",
            task_name=task_name,
        )

    def _task_day_count(self, task_name: str, current: datetime) -> int:
        return evedb.count_proactive_events_since(
            self._day_start(current),
            action_type="task",
            task_name=task_name,
        )

    def _get_due_fixed_tasks(self, current: datetime) -> list[str]:
        """返回当前时间命中固定检查时间、且今天尚未检查过的任务名列表。"""
        due = []
        current_minutes = current.hour * 60 + current.minute
        # 允许在固定时间点前后 check_interval_seconds/2 的窗口内命中
        tolerance = int(self.config.get("check_interval_seconds", 60)) // 2
        for task_name, task_config in (self.config.get("allowed_tasks") or {}).items():
            if not task_config.get("enabled", False):
                continue
            fixed_time = task_config.get("fixed_check_time")
            if not fixed_time:
                continue
            target_minutes = self._hhmm_to_minutes(fixed_time)
            if abs(current_minutes - target_minutes) > tolerance:
                continue
            # 检查今天是否已经针对该任务做过 fixed_time 触发（避免同一分钟重复）
            checked_key = f"fixed_check_done_{task_name}_{current.date().isoformat()}"
            if evedb.get_proactive_setting(checked_key):
                continue
            evedb.set_proactive_setting(checked_key, "1")
            due.append(task_name)
        return due

    def _is_task_time_allowed(self, task_config: dict[str, Any], current: datetime) -> bool:
        windows = task_config.get("allowed_time_windows") or []
        if not windows:
            return True
        current_minutes = current.hour * 60 + current.minute
        for window in windows:
            start, end = window.split("-", 1)
            if self._hhmm_to_minutes(start) <= current_minutes <= self._hhmm_to_minutes(end):
                return True
        return False

    def _build_event_context(self, current: datetime, task_whitelist: list[str] | None = None) -> dict[str, Any]:
        allowed_tasks = {}
        blocked_reasons = []
        for task_name, task_config in (self.config.get("allowed_tasks") or {}).items():
            # 白名单模式：只包含指定任务，且强制启用（跳过概率/时间窗检查）
            if task_whitelist is not None:
                if task_name not in task_whitelist:
                    continue
                task_allowed = bool(task_config.get("enabled", False))
                # 白名单任务仍然检查次数限制
                daily_limit = int(task_config.get("daily_limit", 0) or 0)
                weekly_limit = int(task_config.get("weekly_limit", 0))
                if daily_limit and self._task_day_count(task_name, current) >= daily_limit:
                    task_allowed = False
                    blocked_reasons.append(f"{task_name} daily limit reached")
                if weekly_limit and self._task_week_count(task_name, current) >= weekly_limit:
                    task_allowed = False
                    blocked_reasons.append(f"{task_name} weekly limit reached")
                allowed_tasks[task_name] = {
                    **task_config,
                    "enabled": task_allowed,
                    "name": task_name,
                    "daily_used": self._task_day_count(task_name, current),
                    "weekly_used": self._task_week_count(task_name, current),
                }
                continue

            task_allowed = bool(task_config.get("enabled", False))
            daily_limit = int(task_config.get("daily_limit", 0) or 0)
            weekly_limit = int(task_config.get("weekly_limit", 0))
            trigger_probability = float(task_config.get("trigger_probability", 1.0))
            if task_allowed and trigger_probability < 1.0 and random.random() > trigger_probability:
                task_allowed = False
                blocked_reasons.append(f"{task_name} skipped by trigger probability")
            if daily_limit and self._task_day_count(task_name, current) >= daily_limit:
                task_allowed = False
                blocked_reasons.append(f"{task_name} daily limit reached")
            if weekly_limit and self._task_week_count(task_name, current) >= weekly_limit:
                task_allowed = False
                blocked_reasons.append(f"{task_name} weekly limit reached")
            if not self._is_task_time_allowed(task_config, current):
                task_allowed = False
                blocked_reasons.append(f"{task_name} outside allowed time windows")
            allowed_tasks[task_name] = {
                **task_config,
                "enabled": task_allowed,
                "name": task_name,
                "daily_used": self._task_day_count(task_name, current),
                "weekly_used": self._task_week_count(task_name, current),
            }

        return {
            "event_type": "scheduled_proactive_check",
            "scheduled_at": current.isoformat(),
            "current_time": current.isoformat(),
            "time_bucket": time_bucket(current),
            "minutes_since_user_message": minutes_since(evedb.get_latest_message_at(role="user"), current),
            "minutes_since_last_proactive": minutes_since(evedb.get_latest_proactive_event_at(status="sent"), current),
            "daily_sent_count": self._today_sent_count(current),
            "daily_message_limit": self.config.get("daily_message_limit"),
            "allowed_tasks": allowed_tasks,
            "task_selection_instruction": (
                "allowed_tasks 是可选主动任务列表。每个任务都包含 description/task_info/constraints。"
                "如果选择任务，task.name 必须等于其中一个 key，task.description 应基于配置 description，"
                "task.task_info 和 task.constraints 应合并配置中的默认值，不要自行发明越权任务。"
            ),
            "task_block_reason": "; ".join(blocked_reasons) if blocked_reasons else None,
        }

    def _handle_pipeline_result(self, pipeline: dict[str, Any]) -> None:
        event_id = int(pipeline.get("event_id"))
        chat_result = pipeline.get("chat")
        if not chat_result:
            return
        content = chat_result.get("content", "")
        payload = normalize_chat_output(content)
        if not payload.get("should_send", False):
            evedb.update_proactive_event(event_id, status="skipped", reason="ChatAgent decided not to send")
            return
        messages = payload.get("messages", [])
        self.send_messages(messages, event_id)
        evedb.update_proactive_event(event_id, status="sent", sent_messages=messages)
