"""A shared submission phase within mini-SWE-agent's existing call budget.

The final calls are reserved for reporting the model's own localization guesses.
No extra model calls, gold locations, or automatic guesses are introduced. If the
model still will not submit, mini-SWE-agent records its usual limit/format failure.
"""

from __future__ import annotations

import json
import re

from minisweagent.agents.default import AgentConfig, DefaultAgent
from minisweagent.exceptions import FormatError
from pydantic import Field

from execloc.parse import parse_locations


class LocalizationAgentConfig(AgentConfig):
    budget_warning_calls: int = Field(default=5, ge=1)
    submission_reserve_calls: int = Field(default=3, ge=1)


# Accept the documented literal heredoc, rather than executing another search
# merely because it happens to contain the submission sentinel somewhere.
_SUBMISSION_COMMAND = re.compile(
    r"\A\s*echo[ \t]+COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT[ \t]*"
    r"(?:&&[ \t]*|\r?\n)cat[ \t]+<<[ \t]*"
    r"(?P<quote>['\"])(?P<delimiter>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)[ \t]*\r?\n"
    r"(?P<submission>.*?)\r?\n(?P=delimiter)[ \t]*\s*\Z",
    re.DOTALL,
)
_LOCATION_BLOCK = re.compile(r"\A<<<LOCATIONS\s*(.*?)\s*LOCATIONS>>>\Z", re.DOTALL)

_SUBMIT_INSTRUCTION = """Use only the final submission command described in the task:
```mswea_bash_command
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat <<'EOF'
<<<LOCATIONS
<your JSON list of up to five locations>
LOCATIONS>>>
EOF
```
Replace the placeholder with your best-supported locations from the investigation,
even if uncertain. Do not search, run tests, or edit files in the submission phase.
Do not invent evidence. If you have no candidates, an empty JSON list honestly
records a localization failure. You cannot continue after submission."""


def _is_submission(command: str) -> bool:
    match = _SUBMISSION_COMMAND.fullmatch(command)
    if match is None:
        return False
    submission = match["submission"].strip()
    block = _LOCATION_BLOCK.fullmatch(submission)
    if block is None:
        return False
    try:
        data = json.loads(block[1])
    except json.JSONDecodeError:
        return False
    items = data.get("locations") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return False
    # Permit an explicit abstention without fabricating a nonempty prediction.
    return not items or not parse_locations(submission)[1]


class LocalizationAgent(DefaultAgent):
    """Use identical budget reminders and submission rules in both arms."""

    def __init__(self, model, env, *, config_class=LocalizationAgentConfig, **kwargs):
        super().__init__(model, env, config_class=config_class, **kwargs)
        if self.config.step_limit <= 0:
            raise ValueError("LocalizationAgent needs a positive step_limit.")

    def _submission_phase(self, calls_remaining: int) -> bool:
        return calls_remaining <= self.config.submission_reserve_calls

    def query(self) -> dict:
        remaining = self.config.step_limit - self.n_calls
        milestones = {self.config.budget_warning_calls, self.config.submission_reserve_calls, 1}
        if remaining > 0 and remaining in milestones:
            if self._submission_phase(remaining):
                content = (
                    f"Budget reminder: {remaining} model call(s) remain, including this one. "
                    "The submission phase has started; only a final submission is accepted.\n\n"
                    + _SUBMIT_INSTRUCTION
                )
            else:
                content = (
                    f"Budget reminder: {remaining} model calls remain, including this one. "
                    f"The final {self.config.submission_reserve_calls} calls are reserved for submission. "
                    "Finish investigating and prepare your best-supported ranked locations."
                )
            self.add_messages(self.model.format_message(
                role="user", content=content,
                extra={"budget_reminder": {"calls_remaining": remaining}},
            ))
        # The parent increments n_calls even if model parsing raises FormatError.
        # At the cap it raises LimitsExceeded without making another API call.
        return super().query()

    def execute_actions(self, message: dict) -> list[dict]:
        remaining_including_this = self.config.step_limit - self.n_calls + 1
        if self._submission_phase(remaining_including_this):
            actions = (message.get("extra") or {}).get("actions") or []
            if (
                len(actions) != 1
                or not isinstance(actions[0], dict)
                or not isinstance(actions[0].get("command"), str)
                or not _is_submission(actions[0]["command"])
            ):
                # query() already saved the assistant and its usage. Add only a
                # correction, so rejection neither hides nor duplicates tokens.
                raise FormatError(self.model.format_message(
                    role="user",
                    content=(
                        "Submission phase: that command was not executed. "
                        f"{self.config.step_limit - self.n_calls} model call(s) remain.\n\n"
                        + _SUBMIT_INSTRUCTION
                    ),
                    extra={"submission_rejected": True},
                ))
        return super().execute_actions(message)
