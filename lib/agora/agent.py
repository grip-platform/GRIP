"""AgoraAgent — an LLM participant in a peer reasoning conversation."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from lib.agora.message import Decision, Message, Response
from lib.agora.prompts import default_system_prompt


@dataclass
class AgoraAgent:
    """One LLM in an AGORA conversation. Stateful (keeps own message history).

    chat_fn signature matches lib.hal.tool_runtime contract:
        chat_fn(messages: list[dict], tools: list[dict]) -> dict (anthropic raw)
    """

    agent_id: str
    chat_fn: Callable[..., dict]
    persona: str | None = None
    history: list[Message] = field(default_factory=list)
    defer_count: int = 0
    max_defers: int = 2

    def receive(self, message: Message, history: list[Message] | None = None) -> Response:
        """Process a received message.

        `history` is the full conversation history (typically passed by the
        runner from bus.history(convo_id)) — the single source of truth for
        what every agent has said so far. When omitted (legacy callers), falls
        back to incrementally tracked self.history.
        """
        if history is None:
            self.history.append(message)
            effective_history = self.history
        else:
            self.history = list(history)
            effective_history = self.history
        prompt_messages = self._build_messages(message, effective_history)
        raw = self.chat_fn(messages=prompt_messages, tools=[])
        return self._parse(raw)

    def _build_messages(self, latest: Message, history: list[Message]) -> list[dict]:
        system = default_system_prompt(self.agent_id, self.persona)
        history_text = self._format_history(history, exclude_msg_id=latest.msg_id)
        user_content = (
            f"[Operating context]\n{system}\n\n"
            f"{history_text}\n\n"
            f"Latest from {latest.sender_id}: {latest.content}\n\n"
            "Respond with the JSON shape from your operating context."
        )
        return [{"role": "user", "content": user_content}]

    @staticmethod
    def _format_history(history: list[Message], exclude_msg_id: str) -> str:
        prior = [m for m in history if m.msg_id != exclude_msg_id]
        if not prior:
            return "[no prior messages]"
        lines = [f"  {m.sender_id}: {m.content[:160]}" for m in prior]
        return "Prior:\n" + "\n".join(lines)

    def _parse(self, raw: dict) -> Response:
        """Parse a model turn into a Response. TOTAL — never raises.

        #2924: a model prose preamble (or any non-JSON noise) used to
        reach an uncaught ``json.loads`` and crash *all* grounded
        ``bin/agora-demo`` (the council surface). Now every malformation
        — unparseable text, missing/unknown ``decision``, or an
        incomplete DEFER/STOP shape — degrades to a single fail-loud
        ``Decision.REPLY`` so the conversation continues instead of the
        whole demo dying on one bad turn.
        """
        text = self._extract_text(raw)
        data = self._loads_or_salvage(self._strip_fences(text))
        if data is None:
            data = self._loads_or_salvage(text)
        if not isinstance(data, dict):
            return self._fail_loud(text, "no JSON object in model output")

        try:
            decision = Decision(str(data.get("decision", "")).strip().lower())
        except ValueError:
            return self._fail_loud(
                text, f"unknown/absent decision {data.get('decision')!r}"
            )

        if decision == Decision.DEFER and self._defer_blocked():
            return Response(
                decision=Decision.REPLY,
                content=f"[defer rejected: max {self.max_defers} reached] {data.get('content', '')}",
            )
        if decision == Decision.DEFER:
            self.defer_count += 1
        try:
            return Response(
                decision=decision,
                content=data.get("content", ""),
                follow_up_intent=data.get("follow_up_intent"),
                defer_to=data.get("defer_to"),
                defer_question=data.get("defer_question"),
                stop_rationale=data.get("stop_rationale"),
            )
        except ValueError as exc:
            # Response.__post_init__ rejects an incomplete DEFER/STOP shape;
            # the defer counter was already advanced above, intentionally
            # left so a malformed defer still counts against the budget.
            return self._fail_loud(text, f"incomplete {decision.value} shape: {exc}")

    @staticmethod
    def _loads_or_salvage(text: str) -> dict | None:
        """json.loads, with a first-``{`` balanced-brace salvage fallback.

        Returns the parsed dict, or None if nothing parseable was found.
        Never raises (the whole point of #2924)."""
        for candidate in (text, AgoraAgent._first_balanced_object(text)):
            if not candidate:
                continue
            try:
                obj = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                return obj
        return None

    @staticmethod
    def _first_balanced_object(text: str) -> str | None:
        """Slice the first brace-balanced ``{...}`` object out of noisy
        prose, ignoring braces inside double-quoted JSON strings."""
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return None

    def _fail_loud(self, text: str, reason: str) -> Response:
        """The single fail-loud Response: a REPLY (the only Decision with
        no required extra fields) carrying the parse error + a raw snippet
        so the failure is visible in the transcript, never silent."""
        snippet = text.strip().replace("\n", " ")[:200]
        return Response(
            decision=Decision.REPLY,
            content=f"[AGORA parse error: {reason}] raw: {snippet}",
        )

    def _defer_blocked(self) -> bool:
        return self.defer_count >= self.max_defers

    @staticmethod
    def _extract_text(raw: dict) -> str:
        return "".join(
            block.get("text", "")
            for block in raw.get("content", [])
            if block.get("type") == "text"
        )

    @staticmethod
    def _strip_fences(text: str) -> str:
        text = text.strip()
        if not text.startswith("```"):
            return text
        lines = text.split("\n")
        end = -1 if lines[-1].startswith("```") else len(lines)
        return "\n".join(lines[1:end]).strip()
