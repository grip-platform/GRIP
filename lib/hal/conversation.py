"""HAL Conversation Engine — multi-turn chat without any external harness.

The core standalone loop: user message -> context assembly -> LLM query ->
response -> memory update -> next turn. Works with any HAL provider.

Usage:
    from lib.hal.conversation import Conversation

    conv = Conversation(model="groq/openai/gpt-oss-120b")
    response = conv.send("What is a Knowledge Engine?")
    response = conv.send("How does it help law firms?")  # Multi-turn

    # Opus 4.8: append an instruction delta without restating the system prompt
    response = conv.send("Summarise", sys_update=SysMsgUpdate(text="one line only"))
"""
from typing import Optional

from lib.hal.context_assembler import ContextAssembler
from lib.hal.cost_tracker import CostTracker
from lib.hal.registry import ProviderRegistry, get_registry, parse_model_address
from lib.hal.sentinel import LimitSentinel
from lib.hal.session_manager import SessionManager
from lib.hal.sysmsg import SysMsgUpdate, apply_update
from lib.hal.types import ChatMessage, ChatResponse


class Conversation:
    """Multi-turn conversation engine powered by HAL."""

    def __init__(
        self,
        model: str = "",
        mode: str = "",
        system_prompt: Optional[str] = None,
        registry: Optional[ProviderRegistry] = None,
    ):
        self._registry = registry or get_registry()
        self._sentinel = LimitSentinel(self._registry)
        self._cost = CostTracker()
        self._session = SessionManager()
        self._assembler = ContextAssembler()
        self._model = model or self._registry.routing.default_model
        self._mode = mode
        self._history: list[ChatMessage] = []
        self._system = system_prompt or self._build_system(mode)

    @property
    def model(self) -> str:
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        self._model = value

    @property
    def history(self) -> list[ChatMessage]:
        return list(self._history)

    @property
    def cost(self) -> CostTracker:
        return self._cost

    def send(
        self,
        message: str,
        model: Optional[str] = None,
        sys_update: Optional[SysMsgUpdate] = None,
    ) -> ChatResponse:
        """Send a message and get a response. Maintains conversation history.

        sys_update (Opus 4.8): an optional mid-conversation instruction delta,
        appended cache-preservingly via lib.hal.sysmsg WITHOUT restating the
        system prompt — cuts input cost on long agentic loops. None leaves
        behaviour byte-identical (the delta path is purely additive).
        """
        target = model or self._model

        # Commit to self._history only AFTER a successful dispatch: a failed
        # dispatch must not orphan the user turn (that would put two consecutive
        # user roles on the next send — rejected by strict alternating providers).
        user_msg = ChatMessage(role="user", content=message)
        messages = self._history + [user_msg]
        if sys_update is not None:
            provider_name, _ = parse_model_address(target)
            messages = apply_update(messages, sys_update, provider=provider_name)

        response = self._sentinel.dispatch(
            target, message,
            system=self._system,
            messages=messages,
        )

        self._cost.record_response(response.model, response.provider, response.usage)
        self._history.append(user_msg)
        self._history.append(ChatMessage(role="assistant", content=response.text))

        return response

    def configure(self, model: Optional[str] = None, mode: Optional[str] = None, clear: bool = False) -> None:
        """Configure conversation: switch model, mode, or clear history."""
        if model:
            self._model = model
        if mode:
            self._mode = mode
            self._system = self._build_system(mode)
        if clear:
            self._history.clear()

    def status(self) -> dict:
        """Conversation status: turns, cost, model, session."""
        snap = self._cost.snapshot()
        return {
            "turns": len(self._history) // 2,
            "total_cost_usd": snap.total_usd,
            "total_tokens": snap.input_tokens + snap.output_tokens,
            "model": self._model,
            "session_id": self._session.session_id,
            "cost_by_provider": self._cost.per_provider(),
        }

    def _build_system(self, mode: str) -> str:
        """Build system prompt with HAL self-awareness + optional mode."""
        identity = _build_identity(self._registry)
        if mode:
            bundle = self._assembler.assemble(mode=mode)
            return identity + "\n\n---\n\n" + bundle.system_prompt
        return identity


def _build_identity(registry: ProviderRegistry) -> str:
    """Build HAL's self-aware system prompt from live infrastructure."""
    from lib.hal.discovery import discover_local_providers
    from lib.hal.skill_loader import SkillLoader

    discovered = discover_local_providers(registry, timeout_s=1.0)
    providers = registry.list_providers()
    loader = SkillLoader()
    modes = loader.list_modes()

    provider_lines = []
    for p in providers:
        models = ", ".join(p.models[:3]) if p.models else "auto-discovered"
        privacy = "PRIVATE (on-premises)" if p.type.value == "local" else "cloud"
        provider_lines.append(f"  - {p.name} ({p.type.value}, {privacy}): {models}")

    local_section = ""
    if discovered:
        local_section = f"\nLocal LLMs detected on this machine: {', '.join(discovered)}"
    else:
        for p in providers:
            if p.type.value == "local" and p.models:
                local_section = f"\nLocal LLMs available: {', '.join(p.models)}"
                break

    return f"""You are HAL — a standalone Knowledge Engine.

You are NOT the fictional HAL 9000. You are a real, running system built by CodeTonight.
You operate independently with zero external dependencies.

YOUR INFRASTRUCTURE (live, right now):
- {len(providers)} LLM providers configured:
{chr(10).join(provider_lines)}
- Limit Sentinel: auto-switches providers when any hits a rate limit or quota
- {len(modes)} operating modes available (code, legal, security, research, etc.)
- 83 enforcement hooks (hard DENY gates + soft signals)
- Persistent memory across sessions
- Cost tracking per query, per provider{local_section}

When asked about your capabilities, describe what you ACTUALLY have (listed above).
When asked to list local LLMs, list the ones actually installed on this machine.
When asked about providers, list your real registered providers.

You support any domain. Legal is Domain 0 (law firm case management, contract review,
billing, compliance). But you work for any knowledge work: engineering, finance,
healthcare, construction — any domain that needs AI with privacy and zero limits.

Be direct, accurate, and concise. You are a professional tool, not a chatbot."""
