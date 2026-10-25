"""Versioned records, content identities, and safe external artifact storage."""
import hashlib
import json
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
VERSION = 1


def digest(data):
    if not isinstance(data, bytes):
        data = json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def record(kind, **values):
    return {"kind": kind, "version": VERSION, **values}


def load(path, kind=None):
    value = json.loads(Path(path).read_text())
    if kind and (not isinstance(value, dict) or value.get("kind") != kind or value.get("version") != VERSION):
        raise ValueError(f"expected {kind} version {VERSION}: {path}")
    return value


def validate_output(value, kind):
    """Validate the bundled, intentionally small output-schema vocabulary."""
    schema = load(SKILL / "references" / "schemas" / (kind + ".json"))

    def validate(node, rule, path):
        if "anyOf" in rule:
            for alternative in rule["anyOf"]:
                try:
                    validate(node, alternative, path)
                    return
                except ValueError:
                    pass
            raise ValueError("output schema mismatch: " + path)
        kinds = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
        if "type" in rule and type(node) is not kinds[rule["type"]]:
            raise ValueError("output type mismatch: " + path)
        if "enum" in rule and node not in rule["enum"]:
            raise ValueError("invalid output value: " + path)
        if "minimum" in rule and node < rule["minimum"]:
            raise ValueError("invalid output minimum: " + path)
        if isinstance(node, dict):
            if set(rule.get("required", [])) - node.keys():
                raise ValueError("missing output fields: " + path)
            properties = rule.get("properties", {})
            if rule.get("additionalProperties") is False and node.keys() - properties.keys():
                raise ValueError("unexpected output fields: " + path)
            for name, item in node.items():
                if name in properties:
                    validate(item, properties[name], path + "." + name)
        if isinstance(node, list):
            for index, item in enumerate(node):
                validate(item, rule["items"], path + f"[{index}]")
    validate(value, schema, kind)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    tmp.replace(path)


def external(path, *repositories):
    path = Path(path).resolve()
    package = SKILL
    candidate = SKILL.parent.parent.parent
    if (candidate / "codex" / "skills" / "code-review-c" == SKILL
            and (candidate / ".codex-plugin" / "plugin.json").is_file()):
        package = candidate
    for root in (package, *repositories):
        if path.is_relative_to(Path(root).resolve()):
            raise ValueError(f"private artifacts must be outside repository/package: {path}")
    return path


def instruction_hash():
    return digest({p.relative_to(SKILL).as_posix(): digest(p.read_bytes())
                   for p in sorted(SKILL.rglob("*")) if p.is_file()
                   and "__pycache__" not in p.parts and p.suffix in {".py", ".md", ".json"}})


def local_ref(root, name):
    """Artifact references cannot escape their content-addressed bundle."""
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("artifact reference escapes bundle")
    return path
