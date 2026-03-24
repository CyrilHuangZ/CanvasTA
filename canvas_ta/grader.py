from __future__ import annotations

from typing import Any

from .config import Settings
from .json_utils import extract_json_from_text
from .llm_client import LLMClient


class Grader:
    def __init__(self, settings: Settings, llm: LLMClient):
        self.settings = settings
        self.llm = llm

    def grade_answer(
        self,
        student_text: str,
        standard_answer: str,
        *,
        total_questions: int | None = None,
    ) -> dict[str, Any]:
        total_questions_hint = (
            f"【题目总数】\n本次作业共 {total_questions} 题，请据此在各题间分配扣分，保证总分合理。\n"
            if total_questions
            else ""
        )
        prompt = f"""
你是严格但公平的助教，请依据标准答案和扣分细则给学生答案评分。

【标准答案】
{standard_answer}

{total_questions_hint}

【扣分细则】
{self.settings.deduction_rules}

【评分硬约束】
1) 满分100分。一般情况下，总分应在85-100区间。
2) 只有出现重大漏写、少写、关键步骤缺失或明显答题错误时，才允许显著降分，最低可到70分。
3) 学生答案正确的题目不扣分。
4) 学生答案错误但步骤/思路基本正确：该题仅扣1-2分。
5) 学生答案错误且无有效步骤：该题扣3-4分。
6) 仅输出总分与分题扣分数据，overall_comment 默认留空字符串。

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
