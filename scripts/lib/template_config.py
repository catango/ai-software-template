"""Load and validate the initialize-project configuration (standard library only).

The schema is declarative: `.template/config.schema.toml`. The configuration is TOML.
"""

import json
import re
import tomllib
from pathlib import Path

SCHEMA_VERSION = 1
TYPES = ("string", "integer", "enum", "string-list", "table-list")


class ConfigError(ValueError):
    """The configuration or the schema is invalid; `errors` lists every problem."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def load_schema(path):
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([f"schema is not valid TOML: {exc}"]) from exc
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ConfigError([f"schema_version of the schema must be {SCHEMA_VERSION}"])
    fields = data.get("field")
    if not isinstance(fields, list) or not fields:
        raise ConfigError(["schema has no [[field]] entries"])
    for field in fields:
        if field.get("type") not in TYPES or not field.get("key"):
            raise ConfigError([f"schema field is malformed: {field.get('key')!r}"])
    return fields


def _is_relative_path(value):
    """A relative path; one trailing "/" is allowed and marks a directory prefix."""
    if value.endswith("/"):
        value = value[:-1]
    if not value or value.startswith("/") or "\\" in value:
        return False
    return all(seg not in ("", ".", "..") for seg in value.split("/"))


def _check_string(label, value, spec, errors):
    if not isinstance(value, str):
        errors.append(f"{label}: must be a string")
        return
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        errors.append(f"{label}: must not contain a control character or line break")
        return
    if "max_length" in spec and len(value) > spec["max_length"]:
        errors.append(f"{label}: longer than {spec['max_length']} characters")
    if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
        errors.append(f"{label}: has an invalid form")
    if spec.get("kind") == "relative-path" and not _is_relative_path(value):
        errors.append(f"{label}: must be a relative path without '.', '..', or backslash")


def _check_value(label, value, spec, errors):
    kind = spec["type"]
    if kind == "string":
        _check_string(label, value, spec, errors)
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{label}: must be an integer")
        elif value < spec.get("min", value):
            errors.append(f"{label}: must be at least {spec['min']}")
    elif kind == "enum":
        if value not in spec["values"]:
            errors.append(f"{label}: must be one of {spec['values']}")
    elif kind == "string-list":
        if not isinstance(value, list):
            errors.append(f"{label}: must be a list")
            return
        if len(value) < spec.get("min_items", 0):
            errors.append(f"{label}: needs at least {spec['min_items']} entry")
        item_spec = {k: v for k, v in spec.items() if k in ("max_length", "kind")}
        if "item_pattern" in spec:
            item_spec["pattern"] = spec["item_pattern"]
        for index, item in enumerate(value):
            _check_string(f"{label}[{index}]", item, item_spec, errors)
        if len(set(map(str, value))) != len(value):
            errors.append(f"{label}: entries must be unique")
    elif kind == "table-list":
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            errors.append(f"{label}: must be a list of tables")
            return
        if len(value) < spec.get("min_items", 0):
            errors.append(f"{label}: needs at least {spec['min_items']} entry")
        for index, table in enumerate(value):
            _check_table(f"{label}[{index}]", table, spec["items"], errors)
        unique = spec.get("unique")
        if unique:
            names = [t.get(unique) for t in value]
            if len(set(map(str, names))) != len(names):
                errors.append(f"{label}: '{unique}' must differ between entries")


def _check_table(label, table, item_fields, errors):
    known = {f["key"] for f in item_fields}
    for key in table:
        if key not in known:
            errors.append(f"{label}.{key}: unknown key")
    for spec in item_fields:
        key = spec["key"]
        if key not in table:
            if spec.get("required"):
                errors.append(f"{label}.{key}: is required")
            continue
        _check_value(f"{label}.{key}", table[key], spec, errors)


def _unknown_keys(config, fields):
    """Keys of the configuration that no schema field names."""
    errors = []

    def walk(node, prefix):
        wanted = {f["key"] for f in fields if f["key"].startswith(prefix)}
        for key, value in node.items():
            path = prefix + key
            if path in wanted:
                continue
            if any(w.startswith(path + ".") for w in wanted):
                if isinstance(value, dict):
                    walk(value, path + ".")
                else:
                    errors.append(f"{path}: must be a table")
            else:
                errors.append(f"{path}: unknown key")

    walk({k: v for k, v in config.items() if k != "schema_version"}, "")
    return errors


def _get(config, key):
    node = config
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _set(config, key, value):
    parts = key.split(".")
    node = config
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def validate(config, fields):
    """Validate a parsed configuration. Returns (config with defaults applied, errors)."""
    errors = []
    if config.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version: must be {SCHEMA_VERSION}")
    errors += _unknown_keys(config, fields)
    result = {"schema_version": SCHEMA_VERSION}
    for spec in fields:
        key = spec["key"]
        present, value = _get(config, key)
        if not present:
            if spec.get("required"):
                errors.append(f"{key}: is required")
            elif "default" in spec:
                _set(result, key, spec["default"])
            continue
        _check_value(key, value, spec, errors)
        _set(result, key, value)
    if not errors:
        if result["github"]["approver_login"].lower() == result["github"]["machine_login"].lower():
            errors.append("github.machine_login: must differ from github.approver_login")
    return result, errors


def load_config(path, fields):
    """Parse and validate the configuration file; check the preset files it names.

    Returns the configuration with defaults applied and the resolved preset files
    {"platform_doc": Path | None, "deny_platform": Path | None}. Raises ConfigError.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError([f"cannot read the configuration: {exc.strerror}"]) from exc
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError([f"the configuration is not valid TOML: {exc}"]) from exc
    config, errors = validate(data, fields)
    presets = {"platform_doc": None, "deny_platform": None}
    if not errors:
        base = path.resolve().parent
        for name in presets:
            rel = config.get("preset", {}).get(name)
            if rel is None:
                continue
            target = (base / rel).resolve()
            if not target.is_relative_to(base):
                errors.append(f"preset.{name}: leaves the configuration directory")
            elif not target.is_file():
                errors.append(f"preset.{name}: file not found")
            else:
                presets[name] = target
    if errors:
        raise ConfigError(errors)
    return config, presets


def _q(value):
    return json.dumps(value, ensure_ascii=True)


def _yaml_list(items, indent):
    if not items:
        return " []\n"
    pad = " " * indent
    return "\n" + "".join(f"{pad}- {_q(item)}\n" for item in items)


def project_yaml(config):
    """Render config/project.yaml. Generation-only keys are left out."""
    project, github = config["project"], config["github"]
    out = [
        "# Written by scripts/initialize-project from the configuration it was given.\n",
        "# Project-owned from now on; template-sync never changes it.\n",
        f"schema_version: {config['schema_version']}\n",
        "project:\n",
        f"  name: {_q(project['name'])}\n",
        f"  description: {_q(project['description'])}\n",
        "github:\n",
        f"  owner: {_q(github['owner'])}\n",
        f"  approver_login: {_q(github['approver_login'])}\n",
        f"  approver_id: {github['approver_id']}\n",
        f"  machine_login: {_q(github['machine_login'])}\n",
        "template:\n",
        f"  source: {_q(config['template']['source'])}\n",
        "workflow:\n",
        "  design_repos:" + _yaml_list(config["workflow"]["design_repos"], 4),
        "  docs_roots:" + _yaml_list(config["workflow"]["docs_roots"], 4),
        "  operator_paths:" + _yaml_list(config["workflow"]["operator_paths"], 4),
        "  operator_commands:" + _yaml_list(config["workflow"]["operator_commands"], 4),
        "architectures:\n",
    ]
    for arch in config["architectures"]:
        out.append(f"  - name: {_q(arch['name'])}\n")
        out.append(f"    runner: {_q(arch['runner'])}\n")
        out.append(f"    runner_private: {_q(arch['runner_private'])}\n")
    return "".join(out)
