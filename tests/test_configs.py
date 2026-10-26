import os

import pytest

from execloc import run
from execloc.run import ARM_KEYS, RunError, load_arm_config, model_config


def test_arms_differ_only_in_container():
    static, exec_ = load_arm_config("static"), load_arm_config("exec")
    for key in set(static) | set(exec_):
        if key in ARM_KEYS:
            continue
        assert static.get(key) == exec_.get(key), f"arms differ in {key!r}"
    # Within environment, only the image may differ.
    se, ee = dict(static["environment"]), dict(exec_["environment"])
    se.pop("image"), ee.pop("image")
    assert se == ee
    assert static["repo_mount"] == "readonly" and exec_["repo_mount"] is None


def test_shared_settings():
    cfg = load_arm_config("static")
    assert cfg["model"]["model_kwargs"]["temperature"] == 0
    assert cfg["model"]["model_class"] == "litellm_textbased"
    assert "<<<LOCATIONS" in cfg["agent"]["instance_template"]
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in cfg["agent"]["instance_template"]
    assert "{{task}}" in cfg["agent"]["instance_template"]


def test_arm_file_cannot_change_shared_settings(tmp_path):
    (tmp_path / "base.yaml").write_text("agent: {step_limit: 40}\n")
    (tmp_path / "static.yaml").write_text("arm: static\nagent: {step_limit: 99}\n")
    with pytest.raises(RunError):
        load_arm_config("static", config_dir=tmp_path)


def test_endpoint_required(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)  # no .env here
    monkeypatch.delenv("EXECLOC_BASE_URL", raising=False)
    monkeypatch.setenv("EXECLOC_MODEL", "m")
    with pytest.raises(RunError, match="EXECLOC_BASE_URL"):
        run.endpoint_from_env()


def test_model_config_uses_local_endpoint():
    cfg = load_arm_config("exec")
    m = model_config(cfg, {"base_url": "http://localhost:11434/v1", "model": "devstral-small-2", "api_key": "x"})
    assert m["model_name"] == "openai/devstral-small-2"
    assert m["model_kwargs"]["api_base"] == "http://localhost:11434/v1"
    assert m["model_kwargs"]["temperature"] == 0
