from __future__ import annotations

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

    def download_attachments(self, submission, download_dir: Path) -> list[SubmissionFile]:
        files: list[SubmissionFile] = []
        attachments = getattr(submission, "attachments", []) or []
        for attachment in attachments:
            file_url = getattr(attachment, "url", None)
            filename = getattr(attachment, "filename", None)
            if isinstance(attachment, dict):
                file_url = file_url or attachment.get("url")
                filename = filename or attachment.get("filename")
            if not file_url or not filename:
                continue

            save_name = f"{submission.user['name']}_{filename}"
            save_path = download_dir / save_name
            response = requests.get(file_url, timeout=60)
            response.raise_for_status()
            save_path.write_bytes(response.content)
            files.append(SubmissionFile(name=save_name, path=save_path))
        return files

    def submit_grade_and_comment(self, submission, total_score, comment: str | None = None) -> None:
        payload = {"submission": {"posted_grade": total_score}}
        if comment and comment.strip():
            payload["comment"] = {"text_comment": comment}
        submission.edit(**payload)
