#!/usr/bin/env python3
"""HAL CLI — standalone interactive Knowledge Engine.

Usage:
    python3 -m lib.hal.cli
    python3 -m lib.hal.cli --model groq/openai/gpt-oss-120b
    python3 -m lib.hal.cli --model ollama/mistral:7b   # Fully private
    python3 -m lib.hal.cli --mode legal

Commands: /model, /mode, /cost, /providers, /history, /clear, /quit
"""
import sys
import os

_hal_home = os.environ.get("HAL_HOME", os.path.expanduser("~/.hal") if os.path.isdir(os.path.expanduser("~/.hal")) else os.path.expanduser("~/.claude"))
sys.path.insert(0, _hal_home)

from lib.hal.conversation import Conversation
from lib.hal.discovery import discover_local_providers
from lib.hal.registry import get_registry

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def main() -> None:
    model, mode = _parse_args()
    registry = get_registry()
    discovered = discover_local_providers(registry)

    print(f"\n{BOLD}{RED}HAL{RESET} — Knowledge Engine")
    print(f"{DIM}Any model. Any provider. Zero limits.{RESET}")
    providers = registry.list_providers()
    local_info = f"Local: {', '.join(discovered)} | " if discovered else ""
    print(f"{DIM}{len(providers)} providers | {local_info}Model: {model}{RESET}")

    # Show local models if any
    for p in providers:
        if p.type.value == "local" and p.models:
            print(f"{DIM}Local models: {', '.join(p.models)}{RESET}")
            break
    print()

    conv = Conversation(model=model, mode=mode, registry=registry)

    while True:
        try:
            user_input = input(f"{GREEN}you>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            _show_cost(conv)
            break

        if not user_input:
            continue
        if user_input.startswith("/"):
            if _handle_command(user_input, conv, registry):
                break
            continue

        try:
            response = conv.send(user_input)
            print(f"\n{BOLD}{response.text}{RESET}")
            print(f"{DIM}{response.provider}/{response.model} | "
                  f"{response.latency_ms:.0f}ms | "
                  f"${conv.cost.total_usd:.6f} session{RESET}\n")
        except Exception as e:
            print(f"{RED}Error: {e}{RESET}\n")


def _handle_command(cmd: str, conv: Conversation, registry) -> bool:
    """Handle slash command. Returns True to exit."""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if command in ("/quit", "/exit"):
        _show_cost(conv)
        return True
    elif command == "/model":
        if arg:
            conv.configure(model=arg)
            print(f"{GREEN}Switched to {arg}{RESET}")
        else:
            print(f"{DIM}Current: {conv.model}{RESET}")
    elif command == "/mode":
        if arg:
            conv.configure(mode=arg)
            print(f"{GREEN}Mode: {arg}{RESET}")
        else:
            print(f"{DIM}Use: /mode <name>{RESET}")
    elif command == "/cost":
        s = conv.status()
        print(f"{DIM}Turns: {s['turns']} | Cost: ${s['total_cost_usd']:.6f} | Tokens: {s['total_tokens']}{RESET}")
        for p, c in s["cost_by_provider"].items():
            print(f"  {DIM}{p}: ${c:.6f}{RESET}")
    elif command == "/providers":
        for p in registry.list_providers():
            status = f"{GREEN}ok{RESET}" if p.healthy else f"{RED}down{RESET}"
            models = ", ".join(p.models[:3])
            print(f"  {p.name} ({p.type.value}) [{status}] {DIM}{models}{RESET}")
    elif command == "/history":
        for msg in conv.history:
            clr = GREEN if msg.role == "user" else CYAN
            print(f"  {clr}{msg.role}>{RESET} {msg.content[:100]}")
    elif command == "/clear":
        conv.configure(clear=True)
        print(f"{DIM}Cleared{RESET}")
    else:
        print(f"{YELLOW}Unknown: {command}{RESET}")
    return False


def _show_cost(conv: Conversation) -> None:
    print(f"\n{DIM}Session cost: ${conv.cost.total_usd:.6f}{RESET}")


def _parse_args() -> tuple:
    model, mode = "groq/openai/gpt-oss-120b", ""
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model = args[i + 1]
            i += 2
        elif args[i] == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            i += 2
        else:
            i += 1
    return model, mode


if __name__ == "__main__":
    main()
