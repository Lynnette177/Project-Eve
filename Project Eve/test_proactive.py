#!/usr/bin/env python3
"""Small helper for testing Project Eve proactive scheduler endpoints."""

import argparse
import json
import urllib.error
import urllib.request


def request_json(method: str, url: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc


def pretty(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Project Eve proactive scheduler.")
    parser.add_argument("command", choices=["status", "test", "send", "schedule-now"], help="Action to run.")
    parser.add_argument("--base-url", default="http://127.0.0.1:7100", help="Project Eve Flask base URL.")
    parser.add_argument("--task", help="Force-enable one task in event_context, e.g. weather_reminder.")
    parser.add_argument("--time-bucket", help="Override event_context time_bucket, e.g. morning/evening.")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    if args.command == "status":
        pretty(request_json("GET", f"{base}/proactive/status"))
        return
    if args.command == "schedule-now":
        pretty(request_json("POST", f"{base}/proactive/schedule-now", {}))
        return

    patch = {}
    if args.time_bucket:
        patch["time_bucket"] = args.time_bucket
    if args.task:
        patch["task_block_reason"] = None
    payload = {"send": args.command == "send", "event_context": patch, "force_task": args.task}
    pretty(request_json("POST", f"{base}/proactive/test", payload))


if __name__ == "__main__":
    main()
