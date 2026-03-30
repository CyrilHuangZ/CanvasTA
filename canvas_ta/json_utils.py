import json
import re
from typing import Any


def _repair_json_invalid_backslashes(candidate: str) -> str:
    # Repair common model output issue: LaTeX-style backslashes in JSON strings
    # are emitted as single backslashes (e.g., "\\frac" should be "\\\\frac").
    valid_escapes = set('"\\/bfnrtu')
    chars: list[str] = []
    in_string = False
    i = 0
    n = len(candidate)

    while i < n:
        ch = candidate[i]

        if ch == '"':
            # Count preceding backslashes to determine whether this quote is escaped.
            backslash_count = 0
            j = i - 1
            while j >= 0 and candidate[j] == "\\":
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:
                in_string = not in_string
            chars.append(ch)
            i += 1
            continue

        if in_string and ch == "\\":
            if i + 1 >= n:
                chars.append("\\\\")
                i += 1
                continue

            nxt = candidate[i + 1]
            if nxt in valid_escapes:
                if nxt == "u":
                    hex_part = candidate[i + 2 : i + 6]
                    if len(hex_part) == 4 and all(c in "0123456789abcdefABCDEF" for c in hex_part):
                        chars.append("\\")
                    else:
                        chars.append("\\\\")
                else:
                    chars.append("\\")
                i += 1
                continue

            chars.append("\\\\")
            i += 1
            continue

        chars.append(ch)
        i += 1

    return "".join(chars)


def extract_json_from_text(text: str) -> dict[str, Any]:
    content = text.strip()

    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", content)
    if fenced:
        content = fenced.group(1).strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        repaired = _repair_json_invalid_backslashes(content)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    brace = re.search(r"\{[\s\S]*\}", content)
    if brace:
        candidate = brace.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            repaired = _repair_json_invalid_backslashes(candidate)
            return json.loads(repaired)

    raise ValueError(f"未能从模型输出中提取 JSON: {text}")
