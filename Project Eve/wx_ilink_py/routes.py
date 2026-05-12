"""Flask app factory — 所有 HTTP 路由集中在此文件。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import os

from flask import Flask, Response, jsonify, redirect, render_template, request

if TYPE_CHECKING:
    from .client import ILinkClient
    from ai.proactive_scheduler import ProactiveScheduler
    from ai.agents.base_agent import BaseAgent
    from ai.debug_visualizer import reload_debug_mode as _RDM
    from ai.chat import EveDatabase


def create_app(
    client: "ILinkClient",
    evedb: "EveDatabase | None" = None,
    proactive_scheduler: "ProactiveScheduler | None" = None,
    all_agents: "list[BaseAgent] | None" = None,
    reload_debug_mode: "Callable | None" = None,
    extra_reload_hooks: "list[Callable] | None" = None,
) -> Flask:
    """创建并返回配置好的 Flask 应用。

    Args:
        client: ILinkClient 实例（必须）。
        evedb: EveDatabase 实例，用于主动调度接口。
        proactive_scheduler: ProactiveScheduler 实例。
        all_agents: 所有 Agent 实例列表，用于 /reload-env。
        reload_debug_mode: debug 模式重载函数。
    """
    _here = os.path.dirname(__file__)
    app = Flask(
        "Project Eve",
        template_folder=os.path.join(_here, "templates"),
        static_folder=os.path.join(_here, "static"),
        static_url_path="/static",
    )

    # ------------------------------------------------------------------
    # ILink / 登录相关路由
    # ------------------------------------------------------------------

    @app.get("/")
    def index() -> str:
        autostart = request.args.get("autostart") == "1"
        if autostart and not client.get_status().get("logged_in"):
            client.start_login_async()
        state = client.get_status()
        return render_template("dashboard.html", state=state)

    @app.post("/login")
    def trigger_login() -> Response:
        force_refresh = request.form.get("force_refresh") == "1"
        client.start_login_async(force_refresh=force_refresh)
        return redirect("/")

    @app.post("/logout")
    def trigger_logout() -> Response:
        client.clear_credentials()
        return redirect("/")

    @app.get("/api/status")
    def api_status() -> Response:
        return jsonify(client.get_status())

    @app.get("/memories")
    def memories_page() -> str:
        return render_template("memories.html")

    @app.get("/api/memories")
    def api_memories() -> Response:
        if not evedb:
            return jsonify({"error": "evedb not configured"}), 503
        import json as _json

        # --- messages ---
        recent_msgs = evedb.get_recent_messages(limit=50)
        msg_stats = evedb.get_message_stats()

        # --- task_sessions ---
        tasks = evedb.list_task_sessions(limit=30)
        active_task = evedb.get_active_task_session()

        # --- core_memories ---
        core_mems = evedb.list_core_memories(limit=100)
        core_cats = list({m["category"] for m in core_mems})

        # --- event_memories ---
        event_mems = evedb.list_event_memories(limit=100)
        event_status_counts: dict = {}
        for em in event_mems:
            s = em.get("status", "unknown")
            event_status_counts[s] = event_status_counts.get(s, 0) + 1

        # --- proactive_events ---
        proactive_evts = evedb.fetch_all(
            "SELECT * FROM proactive_events ORDER BY created_at DESC LIMIT 30"
        )
        for pe in proactive_evts:
            try:
                pe["decision"] = _json.loads(pe["decision"]) if pe.get("decision") else None
            except Exception:
                pass
            try:
                pe["sent_messages"] = _json.loads(pe["sent_messages"]) if pe.get("sent_messages") else []
            except Exception:
                pe["sent_messages"] = []

        # --- proactive_settings ---
        ps_rows = evedb.fetch_all("SELECT key, value, updated_at FROM proactive_settings")
        proactive_settings = {r["key"]: r for r in ps_rows}

        return jsonify({
            "msg_stats": msg_stats,
            "recent_messages": recent_msgs,
            "active_task": active_task,
            "tasks": tasks,
            "core_memories": core_mems,
            "core_categories": sorted(core_cats),
            "event_memories": event_mems,
            "event_status_counts": event_status_counts,
            "proactive_events": proactive_evts,
            "proactive_settings": proactive_settings,
        })

    # ------------------------------------------------------------------
    # 系统管理路由
    # ------------------------------------------------------------------

    @app.post("/reload-env")
    def trigger_reload_env() -> Response:
        if all_agents:
            for agent in all_agents:
                agent.reload()
        if proactive_scheduler:
            proactive_scheduler.reload()
        if reload_debug_mode:
            reload_debug_mode()
        for hook in (extra_reload_hooks or []):
            hook()
        reloaded = [a.__class__.__name__ for a in (all_agents or [])]
        return jsonify({"status": "ok", "reloaded_agents": reloaded})

    # ------------------------------------------------------------------
    # 主动调度路由
    # ------------------------------------------------------------------

    @app.get("/proactive/status")
    def proactive_status() -> Response:
        if not proactive_scheduler:
            return jsonify({"error": "proactive_scheduler not configured"}), 503
        return jsonify(proactive_scheduler.status_snapshot())

    @app.post("/proactive/test")
    def proactive_test() -> Response:
        if not proactive_scheduler:
            return jsonify({"error": "proactive_scheduler not configured"}), 503
        payload = request.get_json(silent=True) or {}
        send = bool(payload.get("send", False))
        event_context_patch = payload.get("event_context") or {}
        force_task = payload.get("force_task")
        pipeline = proactive_scheduler.force_run(
            send=send,
            event_context_patch=event_context_patch,
            force_task=force_task,
        )
        return jsonify({
            "ok": True,
            "send": send,
            "event_id": pipeline.get("event_id"),
            "decision": pipeline.get("proactive_decision"),
            "has_performer": bool(pipeline.get("performer")),
            "chat_content": (pipeline.get("chat") or {}).get("content"),
            "error": pipeline.get("error"),
        })

    @app.post("/proactive/schedule-now")
    def proactive_schedule_now() -> Response:
        if not evedb:
            return jsonify({"error": "evedb not configured"}), 503
        evedb.set_proactive_setting("next_proactive_at", evedb.now())
        return jsonify({"ok": True, "next_proactive_at": evedb.get_proactive_setting("next_proactive_at")})

    @app.post("/shutdown")
    def trigger_shutdown() -> Response:
        import signal
        os.kill(os.getpid(), signal.SIGTERM)
        return jsonify({"status": "shutting_down"})

    @app.get("/api/pipeline-status")
    def api_pipeline_status() -> Response:
        import ai.pipeline_state as _ps
        return jsonify(_ps.get_state())

    return app


