"""YAML configuration loader with environment variable interpolation."""

import os
import re
from pathlib import Path
from typing import Any

import yaml


_ENV_VAR_RE = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")


def _interpolate_env(value: str) -> str:
    """Replace ${VAR} and ${VAR:default} with environment variable values."""

    def _replacer(match: re.Match) -> str:
        var_name = match.group(1)
        default = match.group(2)
        return os.environ.get(var_name, default if default is not None else match.group(0))

    return _ENV_VAR_RE.sub(_replacer, value)


def _walk_and_interpolate(obj: Any) -> Any:
    """Recursively interpolate env vars in all string values."""

    if isinstance(obj, str):
        return _interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _walk_and_interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_and_interpolate(v) for v in obj]
    return obj


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    """Load a YAML config file with env var interpolation."""

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return {}

    return _walk_and_interpolate(raw)


def load_agent_config(agent_id: str, config_dir: str = "config/agents") -> dict[str, Any]:
    """Load an agent configuration by ID from the config directory."""
    path = Path(config_dir) / f"{agent_id}.yaml"
    return load_yaml_config(path)


def load_all_agent_configs(config_dir: str = "config/agents") -> dict[str, dict[str, Any]]:
    """Load all agent configurations from a directory."""

    configs = {}
    config_path = Path(config_dir)
    if not config_path.exists():
        return configs

    for yaml_file in config_path.glob("*.yaml"):
        agent_id = yaml_file.stem
        configs[agent_id] = load_yaml_config(yaml_file)

    return configs


def load_intent_routing_config(path: str = "config/intent_routing.yaml") -> dict[str, Any]:
    """Load the intent routing configuration.

    Args:
        path: Path to the intent routing YAML file.

    Returns:
        Dict with 'intents', 'keyword_min_matches', 'keyword_confidence', 'llm_classifier'.
    """
    return load_yaml_config(path)
