import json

import pytest
import yaml

from core.config import EXAMPLE_CONFIG, apply_overrides, load_config, parse_config
from diff_engine import ConfigError


def test_example_config_is_valid(tmp_path):
    p = tmp_path / "vrt.yaml"
    p.write_text(EXAMPLE_CONFIG)
    cfg = load_config(p)
    assert [s.name for s in cfg.scenarios] == ["home", "pricing"]
    assert cfg.diff.color.delta_e == 2.3
    assert cfg.diff_for(cfg.scenarios[1]).color.delta_e == 3.0  # per-scenario override
    assert cfg.diff.decision.fail_pixels == 10
    assert cfg.scenario_url(cfg.scenarios[0]) == "http://localhost:8080/index.html"
    assert cfg.baseline_dir.startswith(str(tmp_path))  # relative paths resolve against the config file


@pytest.mark.parametrize("data, msg", [
    ({"scenarios": [{"name": "a", "path": "/"}], "bogus": 1}, "unknown key"),
    ({"scenarios": [{"name": "a b", "path": "/"}]}, "name must match"),
    ({"scenarios": [{"name": "a"}, {"name": "a"}]}, "duplicate"),
    ({"capture": {"browsers": ["ie6"]}, "scenarios": [{"name": "a"}]}, "unsupported"),
    ({"diff": {"profile": "nope"}, "scenarios": [{"name": "a"}]}, "unknown profile"),
    ({"diff": {"color": {"delta_e": -2}}, "scenarios": [{"name": "a"}]}, "delta_e"),
    ({"on_missing_baseline": "maybe", "scenarios": [{"name": "a"}]}, "on_missing_baseline"),
    ({"scenarios": [{"name": "a", "viewports": ["tablet"]}]}, "unknown viewport"),
])
def test_invalid_configs_are_rejected(data, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_config(data)


def test_env_expansion_and_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("SITE", "http://site:9000")
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"base_url": "${SITE}", "other": "${MISSING:-dflt}", "scenarios": [{"name": "a", "path": "/x"}]}) .replace('"other": "${MISSING:-dflt}", ', ''))
    cfg = load_config(p, ["diff.color.delta_e=4", "diff.profile=lenient"])
    assert cfg.base_url == "http://site:9000"
    assert cfg.diff.color.delta_e == 4 and cfg.diff_profile == "lenient"


def test_apply_overrides_parses_yaml_values():
    d = apply_overrides({}, ["a.b=3", "a.c=[1,2]", "x=hello"])
    assert d == {"a": {"b": 3, "c": [1, 2]}, "x": "hello"}
    with pytest.raises(ConfigError):
        apply_overrides({}, ["novalue"])
