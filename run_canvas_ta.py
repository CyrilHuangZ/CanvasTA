import argparse
import subprocess
import sys

from canvas_ta.pipeline import run_grading_pipeline, submit_approved_results


def run_review_ui() -> None:
    cmd = [sys.executable, "-m", "streamlit", "run", "canvas_ta/review_ui.py"]
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CanvasTA 统一入口：批改、审阅 UI、回传 Canvas"
    )
    parser.add_argument(
        "command",
        choices=["grade", "review", "submit"],
        help="grade=批改并生成 Results; review=打开审阅界面; submit=回传已审核结果",
    )
    args = parser.parse_args()

    if args.command == "grade":
        run_grading_pipeline()
    elif args.command == "review":
        run_review_ui()
    elif args.command == "submit":
        submit_approved_results()


if __name__ == "__main__":
    main()
