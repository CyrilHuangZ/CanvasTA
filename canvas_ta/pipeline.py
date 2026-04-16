from __future__ import annotations

from collections import defaultdict
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .canvas_service import CanvasService
from .config import Settings
from .extractor import AnswerExtractor
from .grader import Grader
from .llm_client import LLMClient


def _result_path(results_dir: Path, student_name: str) -> Path:
    return results_dir / f"{student_name}.json"


def _write_result_with_history(
    *,
    latest_dir: Path,
    history_dir: Path,
    run_id: str,
    student_name: str,
    data: dict[str, Any],
) -> Path:
    latest_path = _result_path(latest_dir, student_name)
    latest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    run_history_dir = history_dir / run_id
    run_history_dir.mkdir(parents=True, exist_ok=True)
    history_path = _result_path(run_history_dir, student_name)
    history_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return latest_path


def _natural_sort_key(value: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def _load_existing_result_data(results_dir: Path, student_name: str) -> dict[str, Any]:
    existing_path = _result_path(results_dir, student_name)
    if not existing_path.exists():
        return {}

    try:
        return json.loads(existing_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _collect_local_submission_groups(
    download_dir: Path,
    student_names: set[str],
) -> dict[str, list[Path]]:
    if not download_dir.exists():
        return {}

    sorted_names = sorted((name for name in student_names if name), key=lambda name: (-len(name), name))
    grouped: dict[str, list[Path]] = defaultdict(list)

    for file_path in sorted(download_dir.glob("*"), key=lambda path: _natural_sort_key(path.name)):
        if not file_path.is_file():
            continue

        matched_student = ""
        for student_name in sorted_names:
            if file_path.stem == student_name or file_path.name.startswith(f"{student_name}_"):
                matched_student = student_name
                break

        # Canvas 自动下载附件命名为: 姓名_附件ID_原文件名；补批改阶段只处理手动放入的文件。
        if matched_student and re.match(rf"^{re.escape(matched_student)}_\d+_", file_path.name):
            continue

        if matched_student:
            grouped[matched_student].append(file_path)

    return {student: files for student, files in grouped.items() if files}


def _grade_local_pending_submissions(
    *,
    settings: Settings,
    results_dir: Path,
    history_dir: Path,
    run_id: str,
    all_submissions: list[Any],
    download_dir: Path,
    extractor: AnswerExtractor,
    grader: Grader,
    standard_answer: str,
    grading_total_questions: int | None,
    retry_failed_only: bool,
    skip_approved: bool,
    reuse_valid_results: bool,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    progress_total: int,
) -> tuple[int, int, int]:
    submissions_by_name = {
        s.user["name"]: s
        for s in all_submissions
        if getattr(s, "user", None) and isinstance(s.user, dict) and s.user.get("name")
    }
    local_groups = _collect_local_submission_groups(download_dir, set(submissions_by_name.keys()))

    if not local_groups:
        return 0, 0, 0

    print(
        f"\n检测到本地手动附件候选: {len(local_groups)} 名学生，"
        "仅对无现有结果 JSON 的学生执行补批改"
    )

    ok_count = err_count = skip_count = 0
    for student_name, source_paths in local_groups.items():
        result_path = _result_path(results_dir, student_name)
        existing_data = _load_existing_result_data(results_dir, student_name)

        if result_path.exists() and not retry_failed_only:
            skip_count += 1
            continue

        if (not reuse_valid_results) and skip_approved and existing_data.get("approved", False):
            skip_count += 1
            continue

        if retry_failed_only and existing_data:
            if not existing_data.get("needs_retry", False):
                skip_count += 1
                continue

        if _is_valid_existing_result(existing_data):
            skip_count += 1
            continue

        submission = submissions_by_name.get(student_name)
        user_id = getattr(submission, "user_id", None) if submission is not None else None

        print(f"\n[{student_name}] 使用本地补交文件补批改")
        if progress_callback:
            progress_callback(
                {
                    "stage": "grading",
                    "message": f"正在补批改本地文件: {student_name}",
                    "current": progress_total,
                    "total": progress_total,
                    "student_name": student_name,
                }
            )

        try:
            route, student_text, parsed_files = extractor.load_student_answers(source_paths)
            if not student_text:
                raise ValueError("提取到的文本为空")

            grading = grader.grade_answer(
                student_text,
                standard_answer,
                total_questions=grading_total_questions,
            )
            result_data: dict[str, Any] = {
                "student_name": student_name,
                "user_id": user_id,
                "assignment_id": settings.assignment_id,
                "run_id": run_id,
                "files": [p.name for p in source_paths],
                "student_source_files": [str(p) for p in source_paths],
                "parsed_source_files": parsed_files,
                "extract_route": route,
                "student_answer_text": student_text,
                "grading": grading,
                "approved": False,
                "needs_retry": False,
                "submission_source": "manual_local_files",
            }
            ok_count += 1
        except Exception as exc:
            retryable = _is_retryable_error(str(exc))
            result_data = {
                "student_name": student_name,
                "user_id": user_id,
                "assignment_id": settings.assignment_id,
                "run_id": run_id,
                "files": [p.name for p in source_paths],
                "student_source_files": [str(p) for p in source_paths],
                "approved": False,
                "error": str(exc),
                "needs_retry": retryable,
                "submission_source": "manual_local_files",
            }
            err_count += 1

        latest_path = _write_result_with_history(
            latest_dir=results_dir,
            history_dir=history_dir,
            run_id=run_id,
            student_name=student_name,
            data=result_data,
        )
        print(f"补批改结果已保存: {latest_path}")

        if progress_callback:
            progress_callback(
                {
                    "stage": "saved",
                    "message": f"已保存补批改结果: {student_name}",
                    "current": progress_total,
                    "total": progress_total,
                    "student_name": student_name,
                }
            )

    return ok_count, err_count, skip_count


def run_grading_pipeline(
    grading_total_questions: int | None = None,
    retry_failed_only: bool = False,
    skip_approved: bool = True,
    reuse_valid_results: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    settings = Settings()
    settings.ensure_dirs()
    results_dir = settings.assignment_results_dir
    download_dir = settings.assignment_download_dir
    history_dir = settings.assignment_history_dir
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    llm = LLMClient(settings)
    extractor = AnswerExtractor(settings, llm)
    grader = Grader(settings, llm)
    canvas = CanvasService(settings)

    answer_file = settings.answer_file
    print(f"标准答案文件: {answer_file}")
    print(f"作业ID: {settings.assignment_id} | 结果目录: {results_dir}")
    standard_answer = extractor.load_standard_answer(answer_file)
    print(f"标准答案加载成功（共 {len(standard_answer)} 字）")
    print(f"课程: {canvas.course.name} | 作业: {canvas.assignment.name}")

    if progress_callback:
        progress_callback(
            {
                "stage": "ready",
                "message": "标准答案加载完成，准备处理学生作业",
            }
        )

    ok_count = err_count = skip_count = 0
    all_submissions = list(canvas.list_submissions())
    submitted = [s for s in all_submissions if s.workflow_state != "unsubmitted"]
    total_submitted = len(submitted)

    if progress_callback:
        progress_callback(
            {
                "stage": "start",
                "message": f"共 {total_submitted} 份已提交作业待处理",
                "current": 0,
                "total": total_submitted,
            }
        )

    processed_submitted = 0
    for submission in all_submissions:
        if submission.workflow_state == "unsubmitted":
            continue

        processed_submitted += 1
        current_no = processed_submitted

        student_name = submission.user["name"]
        user_id = submission.user_id
        print(f"\n[{student_name}] 开始处理")

        existing_data = _load_existing_result_data(results_dir, student_name)

        existing_result_is_valid = _is_valid_existing_result(existing_data)

        should_download_only = reuse_valid_results and existing_result_is_valid
        if should_download_only and retry_failed_only:
            should_download_only = False

        if (not reuse_valid_results) and skip_approved and existing_data.get("approved", False):
            skip_count += 1
            if progress_callback:
                progress_callback(
                    {
                        "stage": "saved",
                        "message": f"跳过（已审核）: {student_name}",
                        "current": current_no,
                        "total": total_submitted,
                        "student_name": student_name,
                    }
                )
            continue

        if retry_failed_only and existing_data:
            if not existing_data.get("needs_retry", False):
                skip_count += 1
                if progress_callback:
                    progress_callback(
                        {
                            "stage": "saved",
                            "message": f"跳过（非重试目标）: {student_name}",
                            "current": current_no,
                            "total": total_submitted,
                            "student_name": student_name,
                        }
                    )
                continue

        if progress_callback:
            progress_callback(
                {
                    "stage": "downloading",
                    "message": f"正在下载作业: {student_name}",
                    "current": current_no,
                    "total": total_submitted,
                    "student_name": student_name,
                }
            )

        attachments = canvas.download_attachments(submission, download_dir)

        if should_download_only:
            source_paths = [a.path for a in attachments]
            expanded_paths = extractor.expand_student_files(source_paths)
            preview_paths = expanded_paths if expanded_paths else source_paths
            updated_data = dict(existing_data)
            updated_data["run_id"] = run_id
            updated_data["files"] = [a.name for a in attachments]
            updated_data["student_source_files"] = [str(p) for p in source_paths]
            # Keep preview stable after reset-download-dir and support archive submissions.
            updated_data["parsed_source_files"] = [str(p) for p in preview_paths]
            updated_data.setdefault("student_name", student_name)
            updated_data.setdefault("user_id", user_id)
            updated_data.setdefault("assignment_id", settings.assignment_id)

            _write_result_with_history(
                latest_dir=results_dir,
                history_dir=history_dir,
                run_id=run_id,
                student_name=student_name,
                data=updated_data,
            )

            skip_count += 1
            if progress_callback:
                progress_callback(
                    {
                        "stage": "saved",
                        "message": f"仅下载并跳过重批（已有有效结果）: {student_name}",
                        "current": current_no,
                        "total": total_submitted,
                        "student_name": student_name,
                    }
                )
            continue

        if not attachments:
            skip_count += 1
            data = {
                "student_name": student_name,
                "user_id": user_id,
                "assignment_id": settings.assignment_id,
                "run_id": run_id,
                "files": [],
                "approved": False,
                "error": "无附件",
            }
            _write_result_with_history(
                latest_dir=results_dir,
                history_dir=history_dir,
                run_id=run_id,
                student_name=student_name,
                data=data,
            )
            if progress_callback:
                progress_callback(
                    {
                        "stage": "saved",
                        "message": f"已保存空附件记录: {student_name}",
                        "current": current_no,
                        "total": total_submitted,
                        "student_name": student_name,
                    }
                )
            continue

        source_paths = [a.path for a in attachments]

        try:
            if progress_callback:
                progress_callback(
                    {
                        "stage": "grading",
                        "message": f"正在批改: {student_name}",
                        "current": current_no,
                        "total": total_submitted,
                        "student_name": student_name,
                    }
                )

            route, student_text, parsed_files = extractor.load_student_answers(source_paths)
            if not student_text:
                raise ValueError("提取到的文本为空")

            grading = grader.grade_answer(
                student_text,
                standard_answer,
                total_questions=grading_total_questions,
            )
            result_data: dict[str, Any] = {
                "student_name": student_name,
                "user_id": user_id,
                "assignment_id": settings.assignment_id,
                "run_id": run_id,
                "files": [a.name for a in attachments],
                "student_source_files": [str(a.path) for a in attachments],
                "parsed_source_files": parsed_files,
                "extract_route": route,
                "student_answer_text": student_text,
                "grading": grading,
                "approved": False,
                "needs_retry": False,
            }
            ok_count += 1
        except Exception as exc:
            retryable = _is_retryable_error(str(exc))
            result_data = {
                "student_name": student_name,
                "user_id": user_id,
                "assignment_id": settings.assignment_id,
                "run_id": run_id,
                "files": [a.name for a in attachments],
                "student_source_files": [str(a.path) for a in attachments],
                "approved": False,
                "error": str(exc),
                "needs_retry": retryable,
            }
            err_count += 1

        latest_path = _write_result_with_history(
            latest_dir=results_dir,
            history_dir=history_dir,
            run_id=run_id,
            student_name=student_name,
            data=result_data,
        )
        print(f"结果已保存: {latest_path}")

        if progress_callback:
            progress_callback(
                {
                    "stage": "saved",
                    "message": f"已保存结果: {student_name}",
                    "current": current_no,
                    "total": total_submitted,
                    "student_name": student_name,
                }
            )

    local_ok, local_err, local_skip = _grade_local_pending_submissions(
        settings=settings,
        results_dir=results_dir,
        history_dir=history_dir,
        run_id=run_id,
        all_submissions=all_submissions,
        download_dir=download_dir,
        extractor=extractor,
        grader=grader,
        standard_answer=standard_answer,
        grading_total_questions=grading_total_questions,
        retry_failed_only=retry_failed_only,
        skip_approved=skip_approved,
        reuse_valid_results=reuse_valid_results,
        progress_callback=progress_callback,
        progress_total=total_submitted,
    )
    ok_count += local_ok
    err_count += local_err
    skip_count += local_skip

    if local_ok or local_err:
        print(
            f"本地补批改完成: 成功 {local_ok} | 出错 {local_err} | 跳过 {local_skip}"
        )

    print("\n" + "=" * 60)
    print(f"完成: 成功 {ok_count} | 出错 {err_count} | 跳过 {skip_count}")
    print(f"请审阅 {results_dir} 中的 JSON，可在审阅 UI 中直接提交，或使用 submit_results.py。")

    if progress_callback:
        progress_callback(
            {
                "stage": "done",
                "message": f"批改完成: 成功 {ok_count} | 出错 {err_count} | 跳过 {skip_count}",
                "current": total_submitted,
                "total": total_submitted,
            }
        )


def _is_retryable_error(error_text: str) -> bool:
    if not error_text:
        return False

    normalized = error_text.lower()
    retry_keywords = [
        "read timed out",
        "timeout",
        "timed out",
        "connection reset",
        "temporarily unavailable",
        "too many requests",
        "429",
        "502",
        "503",
        "504",
    ]
    return any(word in normalized for word in retry_keywords)


def _is_valid_existing_result(data: dict[str, Any]) -> bool:
    if not isinstance(data, dict) or not data:
        return False

    if data.get("error"):
        return False

    grading = data.get("grading")
    if not isinstance(grading, dict):
        return False

    total_score = grading.get("total_score")
    if isinstance(total_score, bool) or not isinstance(total_score, (int, float)):
        return False

    items = grading.get("items")
    if items is not None and not isinstance(items, list):
        return False

    return True


def submit_approved_results() -> None:
    settings = Settings()
    success, skipped, failed = submit_approved_results_with_stats(settings=settings)

    print("\n" + "=" * 60)
    print(f"提交完成: 成功 {success} | 跳过 {skipped} | 失败 {failed}")


def compact_submission_cache(*, dry_run: bool = False) -> tuple[int, int, Path]:
    settings = Settings()
    settings.ensure_dirs()
    download_dir = settings.assignment_download_dir

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = download_dir / "_cache_archive" / run_id

    duplicate_files: list[Path] = []
    # Legacy downloader used suffixes like *_1.pdf, *_2.jpg. Only archive them if base file exists.
    for file_path in download_dir.glob("*"):
        if not file_path.is_file():
            continue

        match = re.match(r"^(?P<base>.+)_(?P<idx>\d+)$", file_path.stem)
        if not match:
            continue

        base_name = match.group("base") + file_path.suffix
        base_path = file_path.with_name(base_name)
        if base_path.exists() and base_path.is_file():
            duplicate_files.append(file_path)

    unzip_dirs = [
        p
        for p in download_dir.glob("*")
        if p.is_dir() and p.name.endswith("__unzipped")
    ]

    if dry_run:
        return len(duplicate_files), len(unzip_dirs), archive_dir

    if duplicate_files or unzip_dirs:
        archive_dir.mkdir(parents=True, exist_ok=True)

    for file_path in duplicate_files:
        shutil.move(str(file_path), str(archive_dir / file_path.name))

    for dir_path in unzip_dirs:
        shutil.move(str(dir_path), str(archive_dir / dir_path.name))

    return len(duplicate_files), len(unzip_dirs), archive_dir


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
    settings: Settings,
) -> tuple[bool, str]:
    student_name = data.get("student_name", "")

    manual_override = bool(data.get("manual_review_override", False))

    if data.get("error") and not manual_override:
        return False, f"跳过 {student_name}: 评分结果有错误"

    if not data.get("approved", False):
        return False, f"跳过 {student_name}: 未审核通过"

    grading = data.get("grading", {})
    total_score = grading.get("total_score")
    if total_score is None:
        return False, f"失败 {student_name}: 缺少总分，无法回传"

    lines = _build_comment_lines(grading)

    submission = submissions_by_name.get(student_name)
    if submission is None:
        return False, f"失败 {student_name}: 未找到对应提交"

    try:
        comment_text = "\n".join(lines) if settings.return_comment_to_canvas else None
        canvas.submit_grade_and_comment(
            submission=submission,
            total_score=total_score,
            comment=comment_text,
        )
        return True, f"已回传 {student_name}: {total_score}"
    except Exception as exc:
        return False, f"回传失败 {student_name}: {exc}"


def _persist_submit_result(
    *,
    file_path: Path,
    data: dict[str, Any],
    ok: bool,
    message: str,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    normalized = message.strip()

    if ok:
        data["canvas_submit_status"] = "success"
        data["canvas_submit_message"] = normalized
        data["canvas_submitted_at"] = now
    elif normalized.startswith("回传失败"):
        data["canvas_submit_status"] = "failed"
        data["canvas_submit_message"] = normalized
        data["canvas_submit_failed_at"] = now
    elif normalized.startswith("跳过"):
        data["canvas_submit_status"] = "skipped"
        data["canvas_submit_message"] = normalized
    else:
        data["canvas_submit_status"] = "failed"
        data["canvas_submit_message"] = normalized
        data["canvas_submit_failed_at"] = now

    file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def submit_single_result_file(file_path: Path) -> tuple[bool, str]:
    settings = Settings()
    canvas = CanvasService(settings)
    submissions_by_name = {
        s.user["name"]
        : s
        for s in canvas.list_submissions()
        if getattr(s, "user", None) and isinstance(s.user, dict) and s.user.get("name")
    }

    data = json.loads(file_path.read_text(encoding="utf-8"))
    ok, message = _submit_one_data(
        data,
        submissions_by_name=submissions_by_name,
        canvas=canvas,
        settings=settings,
    )
    _persist_submit_result(file_path=file_path, data=data, ok=ok, message=message)
    return ok, message


def submit_approved_results_with_stats(
    *,
    settings: Settings | None = None,
    canvas: CanvasService | None = None,
) -> tuple[int, int, int]:
    settings = settings or Settings()
    results_dir = settings.assignment_results_dir

    if not results_dir.exists():
        raise FileNotFoundError("Results/ 不存在，请先运行批改流程。")

    canvas = canvas or CanvasService(settings)
    submissions_by_name = {
        s.user["name"]
        : s
        for s in canvas.list_submissions()
        if getattr(s, "user", None) and isinstance(s.user, dict) and s.user.get("name")
    }

    success = skipped = failed = 0

    for file_path in sorted(results_dir.glob("*.json")):
        data = json.loads(file_path.read_text(encoding="utf-8"))
        ok, message = _submit_one_data(
            data,
            submissions_by_name=submissions_by_name,
            canvas=canvas,
            settings=settings,
        )
        _persist_submit_result(file_path=file_path, data=data, ok=ok, message=message)
        print(message)
        if ok:
            success += 1
        elif "跳过" in message:
            skipped += 1
        else:
            failed += 1

    return success, skipped, failed
