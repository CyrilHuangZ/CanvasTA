from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

try:
    import fitz  # type: ignore
except ImportError:
    fitz = None

try:
    from docx import Document  # type: ignore
except ImportError:
    Document = None

from .config import Settings
from .json_utils import extract_json_from_text
from .llm_client import LLMClient


class AnswerExtractor:
    def __init__(self, settings: Settings, llm: LLMClient):
        self.settings = settings
        self.llm = llm

    def extract_text_from_pdf(self, file_path: Path) -> str:
        if fitz is None:
            raise ImportError("缺少 PyMuPDF，请安装 pymupdf。")
        texts: list[str] = []
        with fitz.open(file_path) as doc:
            for page in doc:
                page_text = page.get_text("text").strip()
                if page_text:
                    texts.append(page_text)
        return "\n\n".join(texts)

    def extract_text_from_docx(self, file_path: Path) -> str:
        if Document is None:
            raise ImportError("缺少 python-docx，请安装 python-docx。")
        doc = Document(file_path)
        return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())

    @staticmethod
    def _file_to_data_url(file_path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(file_path.name)
        mime_type = mime_type or "application/octet-stream"
        encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    def _pdf_to_image_urls(self, file_path: Path) -> list[str]:
        if fitz is None:
            raise ImportError("需要 pymupdf 渲染扫描版 PDF。")
        image_urls: list[str] = []
        with fitz.open(file_path) as doc:
            for idx, page in enumerate(doc):
                if idx >= self.settings.max_vision_pages:
                    break
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                encoded = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                image_urls.append(f"data:image/png;base64,{encoded}")
        return image_urls

    def extract_with_vision(self, file_path: Path) -> dict[str, Any]:
        suffix = file_path.suffix.lower()
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    """你是严格的 OCR/结构化提取助手。任务：从下方学生作业原文中逐题提取答案，严格按要求输出唯一的 JSON，
不要做总结或评分，不要输出任何额外文字或 Markdown 代码块。对公式类内容请转换为 LaTeX（例如 x^2 表示为 $x^2$）。
若能识别题号，按题号放入 questions；若无法识别或为零散内容，放入 raw_text。输出格式必须是如下合法 JSON 且仅输出该 JSON：

{"questions": [{"question_no": "1", "answer": "回答文本，公式已用 LaTeX 表示"}], "raw_text": ""}

额外规则：不要添加解释或多余字段；若文本不可识别，返回 {"questions": [], "raw_text": "<原文片段>"}"""
                ),
            }
        ]

        if suffix == ".pdf":
            image_urls = self._pdf_to_image_urls(file_path)
            for page_no, url in enumerate(image_urls, start=1):
                content.append({"type": "text", "text": f"第{page_no}页"})
                content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            content.append({"type": "image_url", "image_url": {"url": self._file_to_data_url(file_path)}})

        result = self.llm.chat(
            model=self.settings.vision_model,
            messages=[
                {"role": "system", "content": "你是严格的OCR提取助手。"},
                {"role": "user", "content": content},
            ],
            temperature=0,
            max_tokens=4000,
        )
        return extract_json_from_text(self.llm.message_text(result))

    @staticmethod
    def stringify_vision_result(result: dict[str, Any]) -> str:
        questions = result.get("questions") or []
        if questions:
            return "\n\n".join(
                f"题号: {q.get('question_no','?')}\n答案:\n{q.get('answer','')}" for q in questions
            )
        return result.get("raw_text", "").strip()

    def route_and_extract(self, file_path: Path) -> tuple[str, str]:
        suffix = file_path.suffix.lower()
        if suffix in {".txt", ".md"}:
            return "text", file_path.read_text(encoding="utf-8")
        if suffix == ".docx":
            return "text", self.extract_text_from_docx(file_path)
        if suffix == ".pdf":
            text = self.extract_text_from_pdf(file_path)
            if len(re.sub(r"\s+", "", text)) < 120:
                return "vision", text
            return "text", text
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            return "vision", ""
        raise ValueError(f"不支持的文件类型: {suffix}")

    def load_standard_answer(self, answer_file: Path) -> str:
        if not answer_file or not answer_file.exists():
            raise FileNotFoundError(
                f"未找到标准答案文件: {answer_file}\n"
                "请将答案文件放入 Answer/ 目录（文件名包含 ASSIGNMENT_ID，支持 txt/md/pdf/docx/图片），"
                "或设置有效的 ANSWER_FILE。"
            )
        route, text = self.route_and_extract(answer_file)
        if route == "text":
            return text.strip()
        vision_result = self.extract_with_vision(answer_file)
        return self.stringify_vision_result(vision_result)

    def load_student_answer(self, student_file: Path) -> tuple[str, str]:
        route, text = self.route_and_extract(student_file)
        if route == "text":
            return route, text.strip()
        vision_result = self.extract_with_vision(student_file)
        return route, self.stringify_vision_result(vision_result)
