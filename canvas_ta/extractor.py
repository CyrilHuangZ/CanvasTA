from __future__ import annotations

import base64
import mimetypes
import re
import shutil
import tarfile
import zipfile
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
    TEXT_SUFFIXES = {".txt", ".md", ".tex"}
    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic", ".heif"}
    ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz"}

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

    @staticmethod
    def _is_low_quality_pdf_text(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 120:
            return True

        cjk_or_alnum = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", compact)
        ratio = (len(cjk_or_alnum) / len(compact)) if compact else 0.0
        if ratio < 0.35:
            return True

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) >= 25:
            avg_len = sum(len(ln) for ln in lines) / len(lines)
            if avg_len < 5:
                return True

        return False

    def extract_text_from_docx(self, file_path: Path) -> str:
        if Document is None:
            raise ImportError("缺少 python-docx，请安装 python-docx。")
        doc = Document(file_path)
        paragraph_text = "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())

        image_text = self._extract_text_from_docx_images(doc)
        if paragraph_text and image_text:
            return f"{paragraph_text}\n\n===== Word内嵌图片OCR =====\n{image_text}"
        if image_text:
            return image_text
        return paragraph_text

    def _extract_text_from_docx_images(self, doc) -> str:
        image_parts: list[tuple[str, str, bytes]] = []
        seen_rel_ids: set[str] = set()

        for rel in doc.part.rels.values():
            rel_type = getattr(rel, "reltype", "")
            if "image" not in rel_type:
                continue

            rel_id = getattr(rel, "rId", "")
            if rel_id in seen_rel_ids:
                continue
            seen_rel_ids.add(rel_id)

            target = getattr(rel, "target_part", None)
            if target is None:
                continue

            blob = getattr(target, "blob", b"")
            if not blob:
                continue

            content_type = getattr(target, "content_type", "") or ""
            ext = mimetypes.guess_extension(content_type) or ".png"
            image_name = f"embedded_{rel_id}{ext}"
            image_parts.append((image_name, content_type or "image/png", blob))

        if not image_parts:
            return ""

        max_images = 12
        texts: list[str] = []
        for idx, (image_name, mime_type, blob) in enumerate(image_parts[:max_images], start=1):
            try:
                data_url = self._bytes_to_data_url(blob, mime_type=mime_type)
                content: list[dict[str, Any]] = [{"type": "text", "text": self._vision_prompt()}]
                content.append({"type": "text", "text": f"Word内嵌图片第{idx}张: {image_name}"})
                content.append({"type": "image_url", "image_url": {"url": data_url}})

                part_result = self._extract_vision_from_content(content)
                part_text = self.stringify_vision_result(part_result).strip()
                if part_text:
                    texts.append(f"===== 内嵌图片{idx} =====\n{part_text}")
            except Exception:
                continue

        return "\n\n".join(texts)

    @staticmethod
    def _bytes_to_data_url(raw_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        encoded = base64.b64encode(raw_bytes).decode("utf-8")
        return f"data:{mime_type};base64,{encoded}"

    def _compress_pixmap_to_target(self, pixmap, target_bytes: int, base_quality: int) -> bytes:
        quality_candidates = [
            max(20, min(95, base_quality)),
            65,
            55,
            45,
            38,
            32,
            26,
        ]
        quality_candidates = list(dict.fromkeys(quality_candidates))

        best_bytes = b""
        best_len = 10**18
        for quality in quality_candidates:
            candidate = pixmap.tobytes("jpg", jpg_quality=quality)
            candidate_len = len(candidate)
            if candidate_len < best_len:
                best_len = candidate_len
                best_bytes = candidate
            if target_bytes <= 0 or candidate_len <= target_bytes:
                return candidate

        return best_bytes

    def _render_page_to_jpeg_bytes(self, page) -> bytes:
        base_scale = self.settings.vision_render_dpi / 72.0
        if self.settings.vision_max_width > 0:
            expected_width = page.rect.width * base_scale
            if expected_width > self.settings.vision_max_width:
                base_scale = self.settings.vision_max_width / page.rect.width

        target_bytes = self.settings.vision_image_target_kb * 1024
        scale_candidates = [1.0, 0.9, 0.8, 0.72, 0.64, 0.56, 0.5, 0.42]

        best_bytes = b""
        best_len = 10**18
        for scale_ratio in scale_candidates:
            final_scale = base_scale * scale_ratio
            pix = page.get_pixmap(
                matrix=fitz.Matrix(final_scale, final_scale),
                colorspace=fitz.csRGB,
                alpha=False,
            )
            compressed = self._compress_pixmap_to_target(
                pix,
                target_bytes=target_bytes,
                base_quality=self.settings.vision_jpeg_quality,
            )
            compressed_len = len(compressed)
            if compressed_len < best_len:
                best_len = compressed_len
                best_bytes = compressed
            if target_bytes <= 0 or compressed_len <= target_bytes:
                return compressed

        return best_bytes

    def _pdf_to_image_urls(self, file_path: Path) -> list[tuple[int, str]]:
        if fitz is None:
            raise ImportError("需要 pymupdf 渲染扫描版 PDF。")

        image_urls: list[tuple[int, str]] = []
        with fitz.open(file_path) as doc:
            for idx, page in enumerate(doc, start=1):
                jpeg_bytes = self._render_page_to_jpeg_bytes(page)
                image_urls.append((idx, self._bytes_to_data_url(jpeg_bytes, mime_type="image/jpeg")))
        return image_urls

    def _image_to_data_url(self, file_path: Path) -> str:
        if fitz is None:
            raise ImportError("需要 pymupdf 处理图片压缩。")

        with fitz.open(file_path) as doc:
            page = doc.load_page(0)
            jpeg_bytes = self._render_page_to_jpeg_bytes(page)
        return self._bytes_to_data_url(jpeg_bytes, mime_type="image/jpeg")

    @staticmethod
    def _vision_prompt() -> str:
        return (
            """你是严格的 OCR/结构化提取助手。任务：从下方学生作业原文中逐题提取答案，严格按要求输出唯一的 JSON，
不要做总结或评分，不要输出任何额外文字或 Markdown 代码块。对公式类内容请转换为 LaTeX（例如 x^2 表示为 $x^2$）。
若能识别题号，按题号放入 questions；若无法识别或为零散内容，放入 raw_text。输出格式必须是如下合法 JSON 且仅输出该 JSON：

{"questions": [{"question_no": "1", "answer": "回答文本，公式已用 LaTeX 表示"}], "raw_text": ""}

额外规则：不要添加解释或多余字段；若文本不可识别，返回 {"questions": [], "raw_text": "<原文片段>"}"""
        )

    def _extract_vision_from_content(self, content: list[dict[str, Any]]) -> dict[str, Any]:
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

    def extract_with_vision(self, file_path: Path) -> dict[str, Any]:
        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            page_images = self._pdf_to_image_urls(file_path)
            if not page_images:
                return {"questions": [], "raw_text": ""}

            page_texts: list[str] = []
            for page_no, url in page_images:
                content: list[dict[str, Any]] = [{"type": "text", "text": self._vision_prompt()}]
                content.append({"type": "text", "text": f"第{page_no}页"})
                content.append({"type": "image_url", "image_url": {"url": url}})

                part_result = self._extract_vision_from_content(content)
                part_text = self.stringify_vision_result(part_result).strip()
                if part_text:
                    page_texts.append(f"===== 第{page_no}页 =====\n{part_text}")

            return {"questions": [], "raw_text": "\n\n".join(page_texts)}

        content: list[dict[str, Any]] = [{"type": "text", "text": self._vision_prompt()}]
        content.append({"type": "image_url", "image_url": {"url": self._image_to_data_url(file_path)}})
        return self._extract_vision_from_content(content)

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
        if suffix in self.TEXT_SUFFIXES:
            try:
                return "text", file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return "text", file_path.read_text(encoding="gb18030", errors="ignore")
        if suffix == ".docx":
            return "text", self.extract_text_from_docx(file_path)
        if suffix == ".pdf":
            text = self.extract_text_from_pdf(file_path)
            if self._is_low_quality_pdf_text(text):
                return "vision", text
            return "text", text
        if suffix in self.IMAGE_SUFFIXES:
            return "vision", ""
        raise ValueError(f"不支持的文件类型: {suffix}")

    def _extract_zip_file(self, zip_path: Path, output_dir: Path) -> list[Path]:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        extracted_files: list[Path] = []
        output_root = output_dir.resolve()
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue

                target_path = (output_dir / info.filename).resolve()
                if not str(target_path).startswith(str(output_root)):
                    continue

                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as source, target_path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                extracted_files.append(target_path)
        return extracted_files

    def _extract_tar_file(self, tar_path: Path, output_dir: Path) -> list[Path]:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        extracted_files: list[Path] = []
        output_root = output_dir.resolve()
        mode = "r:gz" if tar_path.suffix.lower() in {".gz", ".tgz"} else "r"
        with tarfile.open(tar_path, mode) as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue

                target_path = (output_dir / member.name).resolve()
                if not str(target_path).startswith(str(output_root)):
                    continue

                target_path.parent.mkdir(parents=True, exist_ok=True)
                source = tf.extractfile(member)
                if source is None:
                    continue
                with source, target_path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                extracted_files.append(target_path)
        return extracted_files

    @staticmethod
    def _natural_sort_key(value: str):
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]

    @staticmethod
    def _is_hidden_or_system_file(path: Path) -> bool:
        lower_parts = [p.lower() for p in path.parts]
        if "__macosx" in lower_parts:
            return True
        return path.name.startswith(".") or path.name.lower() in {"thumbs.db", "desktop.ini"}

    def _is_supported_student_file(self, file_path: Path) -> bool:
        suffix = file_path.suffix.lower()
        return suffix in (
            self.TEXT_SUFFIXES
            | self.IMAGE_SUFFIXES
            | {".pdf", ".docx"}
        )

    def _is_archive_file(self, file_path: Path) -> bool:
        lower_name = file_path.name.lower()
        if lower_name.endswith(".tar.gz"):
            return True
        return file_path.suffix.lower() in self.ARCHIVE_SUFFIXES

    def _expand_archive(self, archive_path: Path) -> list[Path]:
        unzip_dir = archive_path.parent / f"{archive_path.stem}__unzipped"
        lower_name = archive_path.name.lower()

        if lower_name.endswith(".zip"):
            return self._extract_zip_file(archive_path, unzip_dir)
        if lower_name.endswith(".tar") or lower_name.endswith(".tgz") or lower_name.endswith(".gz"):
            return self._extract_tar_file(archive_path, unzip_dir)
        return []

    def expand_student_files(self, student_files: list[Path]) -> list[Path]:
        expanded: list[Path] = []
        queue = [p for p in student_files if p.exists() and p.is_file()]
        seen: set[Path] = set()
        max_archive_depth = 2
        archive_depths: dict[Path, int] = {}

        while queue:
            current = queue.pop(0)
            resolved_current = current.resolve()
            if resolved_current in seen:
                continue
            seen.add(resolved_current)

            if self._is_hidden_or_system_file(current):
                continue

            if self._is_archive_file(current):
                depth = archive_depths.get(current, 0)
                if depth >= max_archive_depth:
                    continue
                for extracted in sorted(self._expand_archive(current), key=lambda p: self._natural_sort_key(p.name)):
                    archive_depths[extracted] = depth + 1
                    queue.append(extracted)
                continue

            if self._is_supported_student_file(current):
                expanded.append(current)

        return sorted(expanded, key=lambda p: self._natural_sort_key(p.name))

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

    def load_student_answers(self, student_files: list[Path]) -> tuple[str, str, list[str]]:
        candidates = self.expand_student_files(student_files)
        if not candidates:
            raise ValueError("未发现可解析的附件（支持 pdf/docx/txt/md/tex/图片/zip）")

        merged_parts: list[str] = []
        routes: list[str] = []
        processed_files: list[str] = []

        for file_path in candidates:
            route, text = self.load_student_answer(file_path)
            cleaned = text.strip()
            if not cleaned:
                continue

            routes.append(route)
            processed_files.append(str(file_path))
            merged_parts.append(
                f"[文件: {file_path.name} | 提取方式: {route}]\n{cleaned}"
            )

        if not merged_parts:
            raise ValueError("附件已解析，但未提取到有效文本")

        route_summary = "+".join(sorted(set(routes))) if routes else "unknown"
        return route_summary, "\n\n".join(merged_parts), processed_files
