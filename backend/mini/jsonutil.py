"""Trích JSON từ văn bản model và kiểm tra theo một tập con JSON Schema."""
from __future__ import annotations

import json
import re
from typing import Any


class InvalidOutput(ValueError):
    pass


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    if text is None:
        raise InvalidOutput("không có nội dung trả về")
    s = text.strip()
    candidates = [s]
    candidates += [m.group(1).strip() for m in _FENCE.finditer(s)]
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = s.find(open_c), s.rfind(close_c)
        if a != -1 and b > a:
            candidates.append(s[a : b + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except (json.JSONDecodeError, TypeError):
            continue
    raise InvalidOutput("kết quả không phải JSON hợp lệ")


_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def validate(value: Any, schema: dict, path: str = "$") -> None:
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = False
        for tt in types:
            py = _TYPES[tt]
            if isinstance(value, py) and not (tt in ("integer", "number") and isinstance(value, bool)):
                ok = True
        if not ok:
            raise InvalidOutput(f"{path}: cần kiểu {t}")
    if "enum" in schema and value not in schema["enum"]:
        raise InvalidOutput(f"{path}: giá trị '{value}' không thuộc {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value.strip()) < schema["minLength"]:
            raise InvalidOutput(f"{path}: quá ngắn")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise InvalidOutput(f"{path}: dài hơn {schema['maxLength']} ký tự")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise InvalidOutput(f"{path}: cần ít nhất {schema['minItems']} phần tử")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise InvalidOutput(f"{path}: tối đa {schema['maxItems']} phần tử")
        if "items" in schema:
            for i, v in enumerate(value):
                validate(v, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                raise InvalidOutput(f"{path}: thiếu trường '{k}'")
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                validate(value[k], sub, f"{path}.{k}")
