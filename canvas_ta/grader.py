from __future__ import annotations

from typing import Any

from .config import Settings
from .json_utils import extract_json_from_text
from .llm_client import LLMClient


class Grader:
    def __init__(self, settings: Settings, llm: LLMClient):
        self.settings = settings
        self.llm = llm

    def grade_answer(self, student_text: str, standard_answer: str) -> dict[str, Any]:
        prompt = f"""
你是严格但公平的助教，请依据标准答案和扣分细则给学生答案评分。

【标准答案】
{standard_answer}

【扣分细则】
{self.settings.deduction_rules}

【学生答案】
{student_text}

请只输出合法 JSON，不要输出 Markdown：
{{
  "total_score": 0,
  "items": [{{"question_no":"1","score":0,"max_score":0,"deduction_reason":"","comment":""}}],
  "overall_comment": ""
}}
""".strip()
        result = self.llm.chat(
            model=self.settings.grading_model,
            messages=[
                {"role": "system", "content": "你是只输出 JSON 的自动阅卷助手。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
        return extract_json_from_text(self.llm.message_text(result))
