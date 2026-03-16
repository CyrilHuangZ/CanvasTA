from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .canvas_service import CanvasService
from .config import Settings
from .extractor import AnswerExtractor
from .grader import Grader
from .llm_client import LLMClient


def _result_path(results_dir: Path, student_name: str) -> Path:
    return results_dir / f"{student_name}.json"


def run_grading_pipeline() -> None:
    settings = Settings()
    settings.ensure_dirs()

    llm = LLMClient(settings)
    extractor = AnswerExtractor(settings, llm)
    grader = Grader(settings, llm)
    canvas = CanvasService(settings)

    answer_file = settings.answer_file
    print(f"标准答案文件: {answer_file}")
    standard_answer = extractor.load_standard_answer(answer_file)
    print(f"标准答案加载成功（共 {len(standard_answer)} 字）")
    print(f"课程: {canvas.course.name} | 作业: {canvas.assignment.name}")

    ok_count = err_count = skip_count = 0

    for submission in canvas.list_submissions():
        if submission.workflow_state == "unsubmitted":
            skip_count += 1
            continue

        student_name = submission.user["name"]
        user_id = submission.user_id
        print(f"\n[{student_name}] 开始处理")

        attachments = canvas.download_attachments(submission, settings.download_dir)
        if not attachments:
            skip_count += 1
            data = {
                "student_name": student_name,
                "user_id": user_id,
                "assignment_id": settings.assignment_id,
                "files": [],
                "approved": False,
                "error": "无附件",
            }
            _result_path(settings.results_dir, student_name).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            continue

        target = attachments[0].path

        try:
            route, student_text = extractor.load_student_answer(target)
            if not student_text:
                raise ValueError("提取到的文本为空")

            grading = grader.grade_answer(student_text, standard_answer)
            result_data: dict[str, Any] = {
                "student_name": student_name,
                "user_id": user_id,
                "assignment_id": settings.assignment_id,
                "files": [a.name for a in attachments],
                "student_source_file": str(target),
                "extract_route": route,
                "student_answer_text": student_text,
                "grading": grading,
                "approved": False,
            }
            ok_count += 1
        except Exception as exc:
            result_data = {
                "student_name": student_name,
                "user_id": user_id,
                "assignment_id": settings.assignment_id,
                "files": [a.name for a in attachments],
                "student_source_file": str(target),
                "approved": False,
                "error": str(exc),
            }
            err_count += 1

        _result_path(settings.results_dir, student_name).write_text(
            json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"结果已保存: {_result_path(settings.results_dir, student_name)}")

    print("\n" + "=" * 60)
    print(f"完成: 成功 {ok_count} | 出错 {err_count} | 跳过 {skip_count}")
    print("请审阅 Results/ 中的 JSON，可在审阅 UI 中直接提交，或使用 submit_results.py。")


def submit_approved_results() -> None:
    settings = Settings()
    success, skipped, failed = submit_approved_results_with_stats(settings=settings)

    print("\n" + "=" * 60)
    print(f"提交完成: 成功 {success} | 跳过 {skipped} | 失败 {failed}")


def _build_comment_lines(grading: dict[str, Any]) -> list[str]:
    items = grading.get("items", [])
    overall_comment = grading.get("overall_comment", "")

    lines: list[str] = []
    for item in items:
        q = item.get("question_no", "?")
        score = item.get("score", "-")
        max_score = item.get("max_score", "-")
        reason = item.get("deduction_reason", "")
        comment = item.get("comment", "")
        line = f"题{q}: {score}/{max_score}"
        if reason:
            line += f" | 扣分原因: {reason}"
        if comment:
            line += f" | 评语: {comment}"
        lines.append(line)

    if overall_comment:
        lines.append(f"总评: {overall_comment}")

    return lines


def _submit_one_data(
    data: dict[str, Any],
    *,
    submissions_by_name: dict[str, Any],
    canvas: CanvasService,
) -> tuple[bool, str]:
    student_name = data.get("student_name", "")

    if data.get("error"):
        return False, f"跳过 {student_name}: 评分结果有错误"

    if not data.get("approved", False):
        return False, f"跳过 {student_name}: 未审核通过"

    grading = data.get("grading", {})
    total_score = grading.get("total_score")
    lines = _build_comment_lines(grading)

    submission = submissions_by_name.get(student_name)
    if submission is None:
        return False, f"失败 {student_name}: 未找到对应提交"

    try:
        canvas.submit_grade_and_comment(
            submission=submission,
            total_score=total_score,
            comment="\n".join(lines),
        )
        return True, f"已回传 {student_name}: {total_score}"
    except Exception as exc:
        return False, f"回传失败 {student_name}: {exc}"


def submit_single_result_file(file_path: Path) -> tuple[bool, str]:
    settings = Settings()
    canvas = CanvasService(settings)
    submissions_by_name = {
        s.user["name"]
        : s
        for s in canvas.list_submissions()
        if s.workflow_state != "unsubmitted"
    }

    data = json.loads(file_path.read_text(encoding="utf-8"))
    return _submit_one_data(data, submissions_by_name=submissions_by_name, canvas=canvas)


def submit_approved_results_with_stats(
    *,
    settings: Settings | None = None,
    canvas: CanvasService | None = None,
) -> tuple[int, int, int]:
    settings = settings or Settings()
    results_dir = settings.results_dir

    if not results_dir.exists():
        raise FileNotFoundError("Results/ 不存在，请先运行批改流程。")

    canvas = canvas or CanvasService(settings)
    submissions_by_name = {
        s.user["name"]
        : s
        for s in canvas.list_submissions()
        if s.workflow_state != "unsubmitted"
    }

    success = skipped = failed = 0

    for file_path in sorted(results_dir.glob("*.json")):
        data = json.loads(file_path.read_text(encoding="utf-8"))
        ok, message = _submit_one_data(
            data,
            submissions_by_name=submissions_by_name,
            canvas=canvas,
        )
        print(message)
        if ok:
            success += 1
        elif "跳过" in message:
            skipped += 1
        else:
            failed += 1

    return success, skipped, failed
