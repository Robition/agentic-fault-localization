"""Exercise submission control without Docker, an endpoint, or gold locations."""

from __future__ import annotations

import copy

import pytest
from minisweagent.exceptions import FormatError, Submitted

from execloc.agent import LocalizationAgent
from execloc.parse import count_tokens, parse_locations


def submission(items='[{"file":"candidate.py","function":"f","start_line":2,"end_line":3}]'):
    return (
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat <<'EOF'\n"
        f"<<<LOCATIONS\n{items}\nLOCATIONS>>>\nEOF"
    )


class ScriptedModel:
    def __init__(self, commands):
        self.commands = iter(commands)
        self.calls = 0
        self.inputs = []

    def query(self, messages):
        self.calls += 1
        self.inputs.append(copy.deepcopy(messages))
        command = next(self.commands)
        response = {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}
        if command is None:
            raise FormatError({"role": "user", "content": "Malformed model response", "extra": {"response": response}})
        return {
            "role": "assistant", "content": command,
            "extra": {"actions": [{"command": command}], "response": response},
        }

    def format_message(self, **kwargs):
        return kwargs

    def format_observation_messages(self, message, outputs, template_vars):
        return [{"role": "user", "content": out["output"]} for out in outputs]

    def get_template_vars(self):
        return {}

    def serialize(self):
        return {}


class ScriptedEnvironment:
    def __init__(self):
        self.executed = []

    def execute(self, action):
        command = action["command"]
        self.executed.append(command)
        if command.startswith("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"):
            payload = command.split("\n", 1)[1].rsplit("\n", 1)[0]
            raise Submitted({"role": "exit", "content": payload, "extra": {"exit_status": "Submitted", "submission": payload}})
        return {"returncode": 0, "output": "investigation output"}

    def get_template_vars(self):
        return {}

    def serialize(self):
        return {}


def make_agent(commands, **kwargs):
    model, env = ScriptedModel(commands), ScriptedEnvironment()
    agent = LocalizationAgent(
        model, env, system_template="Localize", instance_template="{{task}}",
        step_limit=kwargs.pop("step_limit", 40), cost_limit=0,
        **kwargs,
    )
    return agent, model, env


def test_reminders_and_successful_submission_stay_within_40_calls():
    agent, model, env = make_agent(["ls"] * 37 + [submission()])
    result = agent.run(task="an issue")
    assert result["exit_status"] == "Submitted"
    assert parse_locations(result["submission"])[0][0]["file"] == "candidate.py"
    assert agent.n_calls == model.calls == 38
    reminders = [m["extra"]["budget_reminder"]["calls_remaining"] for m in agent.messages if "budget_reminder" in m.get("extra", {})]
    assert reminders == [5, 3]
    assert len(env.executed) == 38


def test_exploration_is_rejected_and_corrected_in_reserved_calls():
    agent, model, env = make_agent(["ls"] * 39 + [submission()])
    result = agent.run(task="an issue")
    assert result["exit_status"] == "Submitted"
    assert agent.n_calls == model.calls == 40
    assert env.executed == ["ls"] * 37 + [submission()]
    assert sum(m.get("extra", {}).get("submission_rejected", False) for m in agent.messages) == 2
    assert count_tokens(agent.messages) == (400, 80)
    assert "final submission" in model.inputs[-1][-1]["content"]


def test_noncompliance_remains_failure_without_extra_calls_or_guesses():
    agent, model, env = make_agent(["ls"] * 41)
    result = agent.run(task="an issue")
    assert result == {"exit_status": "RepeatedFormatError", "submission": ""}
    assert agent.n_calls == model.calls == 40
    assert len(env.executed) == 37
    assert count_tokens(agent.messages) == (400, 80)
    assert parse_locations(result["submission"]) == ([], True)


def test_disabling_format_error_cutoff_still_respects_step_limit():
    agent, model, env = make_agent(["ls"] * 41, max_consecutive_format_errors=0)
    result = agent.run(task="an issue")
    assert result["exit_status"] == "LimitsExceeded"
    assert agent.n_calls == model.calls == 40
    assert len(env.executed) == 37


def test_model_format_error_consumes_budget_and_usage_once():
    agent, model, env = make_agent(["ls"] * 37 + [None, submission()])
    result = agent.run(task="an issue")
    assert result["exit_status"] == "Submitted"
    assert agent.n_calls == model.calls == 39
    assert len(env.executed) == 38
    assert count_tokens(agent.messages) == (390, 78)


def test_explicit_abstention_is_preserved_as_localization_failure():
    agent, model, env = make_agent(["ls"] * 37 + [submission("[]")])
    result = agent.run(task="an issue")
    assert result["exit_status"] == "Submitted"
    assert parse_locations(result["submission"]) == ([], True)


def test_malformed_json_and_prefixed_exploration_are_not_executed():
    agent, model, env = make_agent([
        "ls", submission("invalid JSON"), "ls && " + submission(), submission(),
    ], step_limit=4)
    result = agent.run(task="an issue")
    assert result["exit_status"] == "Submitted"
    assert agent.n_calls == model.calls == 4
    assert env.executed == ["ls", submission()]


def test_requires_a_finite_budget():
    with pytest.raises(ValueError, match="positive step_limit"):
        make_agent([], step_limit=0)
