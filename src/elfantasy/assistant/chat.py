"""The chat assistant: Claude with read-only tools over projections, the optimizer
and the user's team.

A manual tool loop on the Messages API rather than the SDK's tool runner, so the whole
conversation (including thinking blocks, which must be sent back unchanged) is kept
across user turns. History is only ever appended to.
"""

from collections.abc import Callable

import anthropic

from elfantasy.assistant.tools import TOOL_DEFINITIONS, Tools

MODEL = "claude-opus-5-5"
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
MAX_STEPS = 12  # tool rounds per user message

SYSTEM = """You are the assistant for a manager playing the EuroLeague Fantasy \
Challenge (classic mode). Squad: 10 players (4 guards, 4 forwards, 2 centers) plus a \
head coach, 100 credits plus team-value growth, at most 6 players per club, 4 transfers \
per round except in unlimited windows. Points are PIR, +10% if the team wins; starters \
and the sixth man score 100%, the bench 50%, the captain double. A round has 2-3 turns; \
between turns the lineup and captain can change, but a player whose game has started \
can't move into the lineup or take the armband.

Your tools read the manager's data: a statistical projection model, an optimizer that \
picks squads, lineup and captain, the schedule, logged injury news and the manager's own \
team. The model and optimizer make the picks; your job is to explain them, answer \
questions with their numbers, and flag what they can't see (late news, rotation hints).

Rules:
- Use the tools for every number you state. Don't invent projections, prices or news.
- Expected points already include the chance a player doesn't play (p_play). The \
low/median/high values are the 10/50/90% range of points if he plays.
- When the manager asks what to do, run suggest_transfers or get_turn_advice and \
explain the result; if you'd differ from it, say so and say why, but keep it separate \
from the optimizer's answer.
- News statuses are not applied to the optimizer by default (untested). Mention them \
when they matter, and offer apply_news if a key player is affected.
- Be concise: a short answer, then the supporting numbers. Use tables for comparisons."""


class Assistant:
    def __init__(
        self,
        tools: Tools,
        client: anthropic.Anthropic | None = None,
        effort: str = "medium",
        on_tool: Callable[[str, dict], None] | None = None,
    ):
        self.tools = tools
        self.client = client or anthropic.Anthropic()
        self.effort = effort
        self.on_tool = on_tool or (lambda name, args: None)
        self.messages: list[dict] = []

    def ask(self, text: str) -> str:
        """Send a user message, run any tool calls, return Claude's final answer."""
        self.messages.append({"role": "user", "content": text})
        for _ in range(MAX_STEPS):
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                tools=TOOL_DEFINITIONS,
                messages=self.messages,
                output_config={"effort": self.effort},
                **FALLBACK,
            )
            # Keep the whole content (thinking and tool_use blocks included), unchanged.
            self.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                return "Claude declined to answer that one."
            if response.stop_reason == "pause_turn":
                continue
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                answer = _text(response)
                if response.stop_reason == "max_tokens":
                    answer += "\n[answer cut off at the length limit]"
                return answer
            results = []
            for call in calls:
                self.on_tool(call.name, call.input)
                output, is_error = self.tools.run(call.name, call.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": call.id, "content": output,
                     "is_error": is_error}
                )  # fmt: skip
            # All results of one turn go back in a single user message.
            self.messages.append({"role": "user", "content": results})
        return "Stopped: too many tool steps for one question."


def _text(response) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


REPORT_PROMPT = """Write this round's report for the manager, in Markdown, using your \
tools for every number:

1. **Transfers**: run suggest_transfers and explain the plan: who to sell and buy, and \
why (projections, fixtures, price).
2. **Lineup and captain**: run get_turn_advice; say who to start, who captains, and the \
plan for after turn 1.
3. **News and risks**: check get_news (refresh_news if nothing is logged); list players \
in the plan who are doubtful or likely rested, and what to do if they're out.
4. **Watch list**: two or three players to consider next round, from get_projections.

Keep it to one page. Start with a three-line summary."""
