import argparse
import shutil
import subprocess
import sys
from datetime import datetime

from canvas_ta.config import Settings
from canvas_ta.pipeline import compact_submission_cache, run_grading_pipeline, submit_approved_results


def run_review_ui() -> None:
    cmd = [sys.executable, "-m", "streamlit", "run", "canvas_ta/review_ui.py"]
    subprocess.run(cmd, check=True)


def _safe_reset_download_dir(target_dir):
    if not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
        return True, f"下载目录不存在，已创建: {target_dir}"

    try:
        shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        return True, f"已重置下载目录: {target_dir}"
    except PermissionError:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantined = target_dir.parent / f"{target_dir.name}__locked_{stamp}"
        try:
            target_dir.rename(quarantined)
            target_dir.mkdir(parents=True, exist_ok=True)
            return True, f"目录占用，已隔离为: {quarantined}；并创建新目录: {target_dir}"
        except Exception as exc:
            return False, (
                f"重置失败（目录被占用）: {target_dir}。"
                f"请关闭资源管理器预览/编辑器占用后重试。详细信息: {exc}"
            )
    except Exception as exc:
        return False, f"重置下载目录失败: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CanvasTA 统一入口：批改、审阅 UI、回传 Canvas"
    )
    parser.add_argument(
        "command",
        choices=["grade", "review", "submit", "clean-cache"],
        help="grade=批改并生成 Results; review=打开审阅界面; submit=回传已审核结果; clean-cache=整理历史重复下载缓存",
    )
    parser.add_argument(
        "--retry-failed-only",
        action="store_true",
        help="仅重试上次记录中可重试错误（如超时/限流）的学生，常用于二次批改。",
    )
    parser.add_argument(
        "--include-approved",
        action="store_true",
        help="默认会跳过已审核学生以避免覆盖结果；开启此项后会重跑已审核学生。",
    )
    parser.add_argument(
        "--reuse-valid-results",
        action="store_true",
        help="若已有正常评分结果（无 error 且有 total_score），则仅下载附件不重批。",
    )
    parser.add_argument(
        "--reset-download-dir",
        action="store_true",
        help="批改前清空当前作业下载目录，仅删除 student_submissions/assignment_xxx，不影响 Results。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览 clean-cache 将处理的文件，不执行实际搬运。",
    )
    args = parser.parse_args()

    if args.command == "grade":
        if args.reset_download_dir:
            settings = Settings()
            target_dir = settings.assignment_download_dir
            ok, msg = _safe_reset_download_dir(target_dir)
            print(msg)
            if not ok:
                sys.exit(1)

        run_grading_pipeline(
            retry_failed_only=args.retry_failed_only,
            skip_approved=not args.include_approved,
            reuse_valid_results=args.reuse_valid_results,
        )
    elif args.command == "review":
        run_review_ui()
    elif args.command == "submit":
        submit_approved_results()
    elif args.command == "clean-cache":
        moved_files, moved_dirs, archive_dir = compact_submission_cache(dry_run=args.dry_run)
        mode = "预览" if args.dry_run else "完成"
        print(
            f"缓存整理{mode}: 文件 {moved_files} 个 | 解压目录 {moved_dirs} 个"
            f" | 归档目录: {archive_dir}"
        )


if __name__ == "__main__":
    main()
