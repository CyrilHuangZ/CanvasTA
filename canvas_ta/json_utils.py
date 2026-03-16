import json
import re
from typing import Any


def extract_json_from_text(text: str) -> dict[str, Any]:
    content = text.strip()

    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", content)
    if fenced:
        content = fenced.group(1).strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    brace = re.search(r"\{[\s\S]*\}", content)
    if brace:
        return json.loads(brace.group(0))

    raise ValueError(f"未能从模型输出中提取 JSON: {text}")
