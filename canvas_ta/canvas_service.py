from __future__ import annotations

import mimetypes
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

import requests
from canvasapi import Canvas
from canvasapi.exceptions import InvalidAccessToken

from .config import Settings


@dataclass
class SubmissionFile:
    name: str
    path: Path


class CanvasService:
    def __init__(self, settings: Settings):
        if not settings.canvas_token:
            raise ValueError("缺少 CANVAS_TOKEN，请在环境变量中配置。")
        self.settings = settings
        self.canvas = Canvas(settings.canvas_url, settings.canvas_token)
        try:
            self.course = self.canvas.get_course(settings.course_id)
            self.assignment = self.course.get_assignment(settings.assignment_id)
        except InvalidAccessToken as exc:
            raise ValueError(
                "Canvas token 无效或已过期。请在 Canvas 网页重新生成 Access Token，"
                "并更新 .env 中的 CANVAS_TOKEN 后重试。"
            ) from exc

    def list_submissions(self):
        return self.assignment.get_submissions(include=["user"])

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        sanitized = re.sub(r'[<>:"/\\|?*]+', "_", name).strip()
        return sanitized or "submission_file"

    @staticmethod
    def _natural_sort_key(value: str):
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]

    @staticmethod
    def _infer_ext_from_content_type(content_type: str | None) -> str:
        if not content_type:
            return ""
        pure_type = content_type.split(";", 1)[0].strip().lower()
        guessed = mimetypes.guess_extension(pure_type) or ""
        return ".jpg" if guessed == ".jpe" else guessed

    def _download_with_retry(self, file_url: str) -> requests.Response:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                resp = requests.get(file_url, timeout=90)
                if resp.status_code in {429, 500, 502, 503, 504}:
                    resp.raise_for_status()
                resp.raise_for_status()
                return resp
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError):
                if attempt >= max_attempts:
                    raise
                time.sleep(1.5 * attempt)

        raise RuntimeError("附件下载失败：超过最大重试次数")

    @staticmethod
    def _parse_canvas_time(raw_value: str | None) -> datetime | None:
        if not raw_value:
            return None

        value = raw_value.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _can_use_cached_file(
        self,
        save_path: Path,
        *,
        attachment_size: int | None,
        attachment_updated_at: str | None,
    ) -> bool:
        if not save_path.exists() or not save_path.is_file():
            return False

        if attachment_size is not None:
            try:
                if save_path.stat().st_size != int(attachment_size):
                    return False
            except Exception:
                return False

        updated_at = self._parse_canvas_time(attachment_updated_at)
        if updated_at is not None:
            file_mtime = datetime.fromtimestamp(save_path.stat().st_mtime, tz=timezone.utc)
            if file_mtime < updated_at:
                return False

        return True

    def download_attachments(self, submission, download_dir: Path) -> list[SubmissionFile]:
        files: list[SubmissionFile] = []
        attachments = getattr(submission, "attachments", []) or []
        attachments = sorted(
            attachments,
            key=lambda item: self._natural_sort_key(
                str(item.get("filename") if isinstance(item, dict) else getattr(item, "filename", ""))
            ),
        )

        for attachment in attachments:
            file_url = getattr(attachment, "url", None)
            filename = getattr(attachment, "filename", None)
            attachment_id = getattr(attachment, "id", None)
            attachment_size = getattr(attachment, "size", None)
            attachment_updated_at = getattr(attachment, "updated_at", None)
            if isinstance(attachment, dict):
                file_url = file_url or attachment.get("url")
                filename = filename or attachment.get("filename")
                attachment_id = attachment_id or attachment.get("id")
                attachment_size = attachment_size or attachment.get("size")
                attachment_updated_at = attachment_updated_at or attachment.get("updated_at")
            if not file_url or not filename:
                continue

            filename = self._sanitize_filename(filename)
            if attachment_id is not None:
                save_name = self._sanitize_filename(
                    f"{submission.user['name']}_{attachment_id}_{filename}"
                )
            else:
                save_name = self._sanitize_filename(f"{submission.user['name']}_{filename}")
            save_path = download_dir / save_name

            if self._can_use_cached_file(
                save_path,
                attachment_size=attachment_size if isinstance(attachment_size, int) else None,
                attachment_updated_at=attachment_updated_at,
            ):
                files.append(SubmissionFile(name=save_path.name, path=save_path))
                continue

            response = self._download_with_retry(file_url)
            if save_path.suffix == "":
                inferred = self._infer_ext_from_content_type(response.headers.get("Content-Type"))
                if inferred:
                    save_path = save_path.with_suffix(inferred)

            save_path.write_bytes(response.content)
            files.append(SubmissionFile(name=save_path.name, path=save_path))
        return files

    def submit_grade_and_comment(self, submission, total_score, comment: str | None = None) -> None:
        payload = {"submission": {"posted_grade": total_score}}
        if comment and comment.strip():
            payload["comment"] = {"text_comment": comment}
        submission.edit(**payload)
