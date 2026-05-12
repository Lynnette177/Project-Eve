"""
Debug Visualizer: 为 debug 模式下的 agent 输出提供可视化展示
"""

import json
import os
from datetime import datetime
from typing import Any
from pathlib import Path
from dotenv import load_dotenv

# Load .env to get DEBUG_MODE
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _is_debug_enabled() -> bool:
    """检查是否启用 debug 模式"""
    return os.getenv("DEBUG_MODE", "false").lower() in ("true", "1", "yes")


DEBUG_MODE = _is_debug_enabled()


# ---------------------------------------------------------------------------
# ANSI Color Codes (for terminal colors)
# ---------------------------------------------------------------------------

class Colors:
    """ANSI color codes for terminal output"""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Foreground colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    # Bright foreground colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"
    
    # Background colors
    BG_BLACK = "\033[40m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"


# Agent-specific colors
AGENT_COLORS = {
    "IntentAgent": Colors.BRIGHT_CYAN,
    "PerformerAgent": Colors.BRIGHT_MAGENTA,
    "ChatAgent": Colors.BRIGHT_GREEN,
    "MemoryAgent": Colors.BRIGHT_YELLOW,
    "BaseAgent": Colors.BRIGHT_BLUE,
}


# ---------------------------------------------------------------------------
# Visualization Helpers
# ---------------------------------------------------------------------------

def _format_json(data: Any, indent: int = 2) -> str:
    """格式化 JSON 数据，支持中文"""
    try:
        if isinstance(data, str):
            # Try to parse if it's a JSON string
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                return data
        return json.dumps(data, indent=indent, ensure_ascii=False)
    except Exception:
        return str(data)


def _colorize(text: str, color: str) -> str:
    """给文本添加颜色（仅在 debug 模式下）"""
    if not DEBUG_MODE:
        return text
    return f"{color}{text}{Colors.RESET}"


def _create_box(title: str, content: str, color: str = Colors.CYAN, width: int = 80) -> str:
    """创建一个带标题的文本框"""
    top = f"╔{'═' * (width - 2)}╗"
    title_line = f"║ {_colorize(title, Colors.BOLD + color)}{' ' * (width - len(title) - 4)}║"
    separator = f"╠{'═' * (width - 2)}╣"
    bottom = f"╚{'═' * (width - 2)}╝"
    
    # Split content into lines and add borders
    content_lines = content.split('\n')
    formatted_lines = []
    for line in content_lines:
        # Handle ANSI color codes in length calculation
        visible_len = len(line)
        padding = width - visible_len - 4
        if padding < 0:
            # Line too long, truncate
            line = line[:width - 7] + "..."
            padding = 0
        formatted_lines.append(f"║ {line}{' ' * padding} ║")
    
    return "\n".join([top, title_line, separator] + formatted_lines + [bottom])


def _create_section(title: str, content: str, color: str = Colors.BLUE) -> str:
    """创建一个分节标题"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    title_colored = _colorize(f"▸ {title}", Colors.BOLD + color)
    time_colored = _colorize(f"[{timestamp}]", Colors.DIM + Colors.BRIGHT_BLACK)
    return f"\n{time_colored} {title_colored}\n{content}"


# ---------------------------------------------------------------------------
# Main Visualizer Functions
# ---------------------------------------------------------------------------

def visualize_agent_output(
    agent_name: str,
    output: dict[str, Any] | str,
    extra_info: dict[str, Any] | None = None,
) -> None:
    """
    可视化展示 Agent 的输出
    
    Args:
        agent_name: Agent 名称（如 "IntentAgent"）
        output: Agent 的输出（dict 或 JSON string）
        extra_info: 额外信息（如输入、配置等）
    """
    if DEBUG_MODE:
        _visualize_debug(agent_name, output, extra_info)
    else:
        _visualize_simple(agent_name, output)


def _visualize_debug(
    agent_name: str,
    output: dict[str, Any] | str,
    extra_info: dict[str, Any] | None = None,
) -> None:
    """Debug 模式下的详细可视化"""
    color = AGENT_COLORS.get(agent_name, Colors.BRIGHT_WHITE)
    
    # Header
    print("\n" + "=" * 80)
    header = f"🤖 {agent_name} Output"
    print(_colorize(header, Colors.BOLD + color))
    print("=" * 80)
    
    # Main output
    if isinstance(output, dict):
        formatted = _format_json(output)
    elif isinstance(output, str):
        # Try to parse as JSON
        try:
            parsed = json.loads(output)
            formatted = _format_json(parsed)
        except json.JSONDecodeError:
            formatted = output
    else:
        formatted = str(output)
    
    print(_create_section("Output", formatted, color))
    
    # Extra info
    if extra_info:
        for key, value in extra_info.items():
            formatted_value = _format_json(value) if isinstance(value, (dict, list)) else str(value)
            print(_create_section(key.replace("_", " ").title(), formatted_value, Colors.BLUE))
    
    print("=" * 80 + "\n")


def _visualize_simple(agent_name: str, output: dict[str, Any] | str) -> None:
    """非 Debug 模式下的简单日志"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # Extract key info for simple log
    if isinstance(output, dict):
        content = output.get("content", str(output))
    elif isinstance(output, str):
        try:
            parsed = json.loads(output)
            content = parsed.get("content", output)
        except json.JSONDecodeError:
            content = output
    else:
        content = str(output)
    
    # Truncate if too long
    if len(content) > 200:
        content = content[:197] + "..."
    
    print(f"[{timestamp}] [{agent_name}] {content}")


# ---------------------------------------------------------------------------
# Pipeline Visualizer
# ---------------------------------------------------------------------------

def visualize_pipeline_start(user_message: str) -> None:
    """可视化 Pipeline 开始"""
    if DEBUG_MODE:
        print("\n" + "▓" * 80)
        print(_colorize("🚀 Pipeline Started", Colors.BOLD + Colors.BRIGHT_WHITE))
        print("▓" * 80)
        print(_create_section("User Message", user_message, Colors.BRIGHT_YELLOW))
    else:
        print(f"[Pipeline] Start: {user_message[:100]}{'...' if len(user_message) > 100 else ''}")


def visualize_pipeline_end() -> None:
    """可视化 Pipeline 结束"""
    if DEBUG_MODE:
        print("\n" + "▓" * 80)
        print(_colorize("✅ Pipeline Completed", Colors.BOLD + Colors.BRIGHT_GREEN))
        print("▓" * 80 + "\n")
    else:
        print("[Pipeline] Completed")


def visualize_step(step_name: str, description: str = "") -> None:
    """可视化 Pipeline 步骤"""
    if DEBUG_MODE:
        print("\n" + "─" * 80)
        print(_colorize(f"⚡ Step: {step_name}", Colors.BOLD + Colors.BRIGHT_CYAN))
        if description:
            print(_colorize(f"   {description}", Colors.DIM + Colors.WHITE))
        print("─" * 80)
    else:
        print(f"[Pipeline] {step_name}")


# ---------------------------------------------------------------------------
# Error Visualizer
# ---------------------------------------------------------------------------

def visualize_error(agent_name: str, error: Exception, context: dict[str, Any] | None = None) -> None:
    """可视化错误信息"""
    if DEBUG_MODE:
        print("\n" + "!" * 80)
        print(_colorize(f"❌ Error in {agent_name}", Colors.BOLD + Colors.BRIGHT_RED))
        print("!" * 80)
        print(_create_section("Error Type", type(error).__name__, Colors.RED))
        print(_create_section("Error Message", str(error), Colors.RED))
        if context:
            print(_create_section("Context", _format_json(context), Colors.YELLOW))
        print("!" * 80 + "\n")
    else:
        print(f"[ERROR] [{agent_name}] {type(error).__name__}: {error}")


# ---------------------------------------------------------------------------
# Utility: Reload debug mode
# ---------------------------------------------------------------------------

def reload_debug_mode() -> None:
    """重新加载 DEBUG_MODE 配置（在 /reload-env 时调用）"""
    global DEBUG_MODE
    load_dotenv(_ENV_PATH, override=True)
    DEBUG_MODE = _is_debug_enabled()
    print(f"[DebugVisualizer] DEBUG_MODE reloaded: {DEBUG_MODE}")
