from wx_ilink_py import ILinkClient, create_app
from ai.chat import evedb, run_pipeline
from ai.chat_output import normalize_chat_output
from ai.agents import IntentAgent, PerformerAgent, ChatAgent, MemoryAgent, ProactiveAgent
from ai.debug_visualizer import reload_debug_mode
from ai.proactive_scheduler import ProactiveScheduler
from threading import Thread, Timer, Lock
from dotenv import load_dotenv
import time
import sys
import os
import json
import atexit
import datetime

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

atexit.register(evedb.close)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
client = ILinkClient(data_dir="./data", auto_login=False)

# --- Agent 实例（持久化，MCP 不重复启动）---
intent_agent = IntentAgent()
performer_agent = PerformerAgent()
chat_agent = ChatAgent()
memory_agent = MemoryAgent()
proactive_agent = ProactiveAgent()

_ALL_AGENTS = [intent_agent, performer_agent, chat_agent, memory_agent, proactive_agent]


def _send_pipeline_messages(messages: list[dict], proactive_event_id: int | None = None):
    sent_messages = []
    for msg in messages:
        msg_type = msg.get("type")
        msg_content = msg.get("content")
        if msg_type == "text" and msg_content:
            client.send_text_simulated(msg_content)
            evedb.add_assistant_message('text', msg_content)
            sent_messages.append(msg)
    if proactive_event_id is not None and sent_messages:
        evedb.update_proactive_event(proactive_event_id, status="sent", sent_messages=sent_messages)


proactive_scheduler = ProactiveScheduler(
    proactive_agent=proactive_agent,
    chat_agent=chat_agent,
    performer_agent=performer_agent,
    send_messages=_send_pipeline_messages,
)


def _load_debounce_config() -> tuple[float, float]:
    """从 .env 读取 DEBOUNCE_NORMAL / DEBOUNCE_EXTRA，返回 (normal, extra)。"""
    normal = float(os.environ.get("DEBOUNCE_NORMAL", "5.0"))
    extra  = float(os.environ.get("DEBOUNCE_EXTRA", "10.0"))
    return normal, extra

def reload_debounce_config() -> None:
    """重新从 .env 加载防抖参数，在 /reload-env 时调用。"""
    global _DEBOUNCE_NORMAL, _DEBOUNCE_EXTRA
    load_dotenv(_ENV_PATH, override=True)
    _DEBOUNCE_NORMAL, _DEBOUNCE_EXTRA = _load_debounce_config()
    print(f"[debounce] reloaded: normal={_DEBOUNCE_NORMAL}s extra={_DEBOUNCE_EXTRA}s")

_DEBOUNCE_NORMAL, _DEBOUNCE_EXTRA = _load_debounce_config()
_pending_messages: list[str] = []
_debounce_timer: Timer | None = None
_buffer_lock = Lock()



app = create_app(
    client=client,
    evedb=evedb,
    proactive_scheduler=proactive_scheduler,
    all_agents=_ALL_AGENTS,
    reload_debug_mode=reload_debug_mode,
    extra_reload_hooks=[reload_debounce_config],
)


# ---------------------------------------------------------------------------
# Turn completion detection (rule-based, no LLM)
# ---------------------------------------------------------------------------

_INCOMPLETE_PREFIXES = ("我跟你说", "就是", "那个", "你知道吗")
_INCOMPLETE_SUFFIXES = ("，", "然后", "但是", "因为", "就是")


def _is_incomplete(messages: list[str]) -> bool:
    """
    Return True if the buffered messages look unfinished and we should wait longer.

    Rules (applied to the last message):
    - Single message, length < 5, and starts with an open-ended opener.
    - Last message ends with a dangling word/punctuation that implies more is coming.
    """
    if not messages:
        return False
    last = messages[-1].strip()
    if len(messages) == 1 and len(last) < 5:
        if any(last.startswith(p) for p in _INCOMPLETE_PREFIXES):
            return True
    if any(last.endswith(s) for s in _INCOMPLETE_SUFFIXES):
        return True
    return False


# ---------------------------------------------------------------------------
# Message buffer + debounce timer
# ---------------------------------------------------------------------------

def _flush():
    """Flush the buffer and run the pipeline. Called from the timer thread."""
    global _pending_messages, _debounce_timer
    with _buffer_lock:
        if not _pending_messages:
            return
        combined = "\n".join(_pending_messages)
        _pending_messages = []
        _debounce_timer = None
    client.send_typing(status=2)
    try:
        _process_message(combined)
    except Exception as e:
        print(f"[_flush] pipeline error, timer thread recovered: {e}")


def _schedule(delay: float):
    """(Re)start the debounce timer with the given delay. Must hold _buffer_lock."""
    global _debounce_timer
    if _debounce_timer is not None:
        _debounce_timer.cancel()
    _debounce_timer = Timer(delay, _flush)
    _debounce_timer.daemon = True
    _debounce_timer.start()


def Eve_Message(message: str):
    """Receive an incoming message and add it to the debounce buffer."""
    global _pending_messages
    proactive_scheduler.on_user_message()
    with _buffer_lock:
        _pending_messages.append(message)
        delay = _DEBOUNCE_EXTRA if _is_incomplete(_pending_messages) else _DEBOUNCE_NORMAL
        _schedule(delay)
    client.send_typing(status=1)
    print(f"[buffer] +msg ({len(_pending_messages)} buffered), next flush in {delay}s")


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def _process_message(combined_message: str):
    """Run the full agent pipeline on a completed user turn."""
    pipeline = run_pipeline(
        user_message=combined_message,
        intent_agent=intent_agent,
        chat_agent=chat_agent,
        performer_agent=performer_agent,
        memory_agent=memory_agent,
    )
    # Save after pipeline so context built inside doesn't include this turn yet
    evedb.add_user_message('text', combined_message)

    chat_result = pipeline["chat"]
    content_str = chat_result.get("content", "")
    content = normalize_chat_output(content_str)

    if content.get("should_send", False):
        _send_pipeline_messages(content.get("messages", []))


def Eve_File(filetype, filePath):
    pass


def _start_daily_decay_scheduler():
    """在后台线程中，每天凌晨 03:00 调用 decay_event_memories。"""
    def _loop():
        while True:
            now = datetime.datetime.now()
            next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if next_run <= now:
                next_run += datetime.timedelta(days=1)
            wait_seconds = (next_run - now).total_seconds()
            time.sleep(wait_seconds)
            try:
                result = evedb.decay_event_memories()
                print(f"[decay] daily memory decay done: {result}")
            except Exception as e:
                print(f"[decay] error: {e}")
    Thread(target=_loop, daemon=True).start()


def mainLoop():
    client.send_text("Project Eve Online.")
    proactive_scheduler.start()
    _start_daily_decay_scheduler()
    consecutive_poll_errors = 0
    while True:
        try:
            messages, _ = client.fetch_messages(timeout=3)
            consecutive_poll_errors = 0
        except RuntimeError as e:
            consecutive_poll_errors += 1
            err = str(e)
            if "errcode=-14" in err or "session timeout" in err.lower():
                print(f"[mainLoop] session timeout，自动清除登录态并重新获取二维码...")
                client.clear_credentials()
                client.start_login_async(force_refresh=True)
            else:
                print(f"[mainLoop] fetch_messages error: {e}")
            time.sleep(min(30, 5 * consecutive_poll_errors))
            continue
        except Exception as e:
            consecutive_poll_errors += 1
            print(f"[mainLoop] unexpected polling error, will retry: {e}")
            time.sleep(min(30, 5 * consecutive_poll_errors))
            continue
        for index, message in enumerate(messages[:10], start=1):
            for item in message.get("raw", {}).get("item_list", []):
                item_type = item.get("type")
                if item_type in (2, 3, 4, 5):  # IMAGE, VOICE, FILE, VIDEO
                    path = client.download_media(item)
                    Eve_File(item_type, path)
                elif item_type == 1:  # TEXT
                    content = item.get("text_item", {}).get("text", "")
                    Eve_Message(content)

        time.sleep(1)


if __name__ == "__main__":
    import signal
    from werkzeug.serving import make_server

    saved = client._load_credentials()
    if saved:
        client.ensure_login()
        Thread(target=mainLoop, daemon=True).start()
    else:
        def _wait_and_start():
            import time as _time
            while not client.state.logged_in:
                _time.sleep(1)
            Thread(target=mainLoop, daemon=True).start()
        Thread(target=_wait_and_start, daemon=True).start()

    server = make_server("0.0.0.0", 7100, app, threaded=True)
    server.socket.setsockopt(__import__('socket').SOL_SOCKET, __import__('socket').SO_REUSEADDR, 1)

    def _handle_sigterm(signum, frame):
        print("[main] SIGTERM received, shutting down server...")
        Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[main] KeyboardInterrupt, shutting down server...")
        server.shutdown()
    sys.exit(0)
