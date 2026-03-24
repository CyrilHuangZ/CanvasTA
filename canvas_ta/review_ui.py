from __future__ import annotations

import base64
import json
from datetime import datetime
from io import BytesIO
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

try:
    from docx import Document  # type: ignore
except Exception:
    Document = None

try:
    from canvas_ta.config import Settings
    from canvas_ta.pipeline import (
        run_grading_pipeline,
        submit_approved_results_with_stats,
        submit_single_result_file,
    )
except ImportError:
    from config import Settings
    from pipeline import run_grading_pipeline, submit_approved_results_with_stats, submit_single_result_file


st.set_page_config(page_title="CanvasTA 审阅台", layout="wide")

settings = Settings()
results_dir = settings.assignment_results_dir
project_root = Path(__file__).resolve().parent.parent
logo_path = project_root / "Logo" / "CanvasTA.png"

if logo_path.exists():
    st.image(str(logo_path), width=180)

st.title("CanvasTA 一体化工作台")
st.caption(f"支持一站式完成批改、审阅、审核标记与回传。当前作业ID: {settings.assignment_id}")
# 主操作界面已移除顶部声明，免责声明保留在侧边栏以避免干扰主操作流程

with st.sidebar:
    st.header("流程控制")
    total_questions_input = st.number_input(
        "题目总数（可选）",
        min_value=0,
        value=settings.total_questions or 0,
        step=1,
        help="用于让模型在总分和分题扣分上更稳定；留空可填0。",
    )

    allow_run_with_zero = False
    if total_questions_input == 0:
        st.warning("题目总数当前为 0，请重新检查。")
        allow_run_with_zero = st.checkbox(
            "我已重新检查，确认本次按题目总数为 0 执行",
            value=False,
            key="confirm_zero_questions",
        )

    progress_text_slot = st.empty()
    progress_bar_slot = st.progress(0)

    can_start_grading = total_questions_input > 0 or allow_run_with_zero
    if st.button("1) 拉取并批改作业", use_container_width=True, disabled=not can_start_grading):
        def _on_grading_progress(event: dict) -> None:
            stage = str(event.get("stage", ""))
            current = int(event.get("current", 0) or 0)
            total = int(event.get("total", 0) or 0)
            stage_label = {
                "ready": "准备完成",
                "start": "开始批改",
                "downloading": "下载作业中",
                "grading": "批改作业中",
                "saved": "结果保存中",
                "done": "批改完成",
            }.get(stage, "处理中")

            if total > 0:
                progress_value = min(100, max(0, int((current / total) * 100)))
                progress_bar_slot.progress(progress_value)
                progress_text_slot.info(
                    f"{stage_label}：第 {min(current, total)} / {total} 份 | {event.get('message', '')}"
                )
            else:
                progress_bar_slot.progress(0)
                progress_text_slot.info(f"{stage_label}：{event.get('message', '')}")

        with st.spinner("正在批改中，这可能需要几分钟..."):
            run_grading_pipeline(
                grading_total_questions=int(total_questions_input) if total_questions_input > 0 else None,
                progress_callback=_on_grading_progress,
            )
        progress_bar_slot.progress(100)
        progress_text_slot.success("批改任务已完成")
        st.success("批改完成，已刷新结果列表")
        st.rerun()

    if st.button("2) 提交全部已审核结果", use_container_width=True):
        success, skipped, failed = submit_approved_results_with_stats()
        if failed == 0:
            st.success(f"提交完成: 成功 {success} | 跳过 {skipped} | 失败 {failed}")
        else:
            st.error(f"提交完成: 成功 {success} | 跳过 {skipped} | 失败 {failed}")

    st.divider()
    st.subheader("免责声明")
    st.error("请认真负责审查学生作业，保证批改质量。")
    st.warning("本项目仅供教学辅助与学习交流，禁止倒卖。")


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _render_pdf(file_path: Path) -> None:
    data = file_path.read_bytes()
    encoded = base64.b64encode(data).decode("utf-8")
    iframe = (
        f'<iframe src="data:application/pdf;base64,{encoded}" '
        'width="100%" height="700" type="application/pdf"></iframe>'
    )
    components.html(iframe, height=720, scrolling=True)


def _render_docx(file_path: Path) -> None:
    if Document is None:
        st.info("当前环境缺少 python-docx，无法内嵌预览 docx，可下载后本地打开。")
        st.download_button("下载原始 DOCX", data=file_path.read_bytes(), file_name=file_path.name)
        return
    doc = Document(BytesIO(file_path.read_bytes()))
    raw_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    st.text_area("原始 DOCX 文本", value=raw_text, height=520, disabled=True)


def _render_source_file(file_path: Path) -> None:
    if not file_path.exists():
        st.warning(f"附件文件不存在: {file_path}")
        return

    suffix = file_path.suffix.lower()
    st.caption(f"文件: {file_path.name} ({suffix or '未知类型'})")

    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
        st.image(str(file_path), use_container_width=True)
        return

    if suffix == ".pdf":
        _render_pdf(file_path)
        return

    if suffix in {".txt", ".md", ".csv", ".json", ".py"}:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        st.text_area("原始文本", value=text, height=520, disabled=True)
        return

    if suffix == ".docx":
        _render_docx(file_path)
        return

    st.info("该文件类型暂不支持内嵌预览，可下载后查看。")
    st.download_button("下载原始附件", data=file_path.read_bytes(), file_name=file_path.name)


def _save_current_file(current_path: Path, data: dict, key_prefix: str) -> None:
    grading = data.setdefault("grading", {})
    items = grading.get("items", [])

    for i, item in enumerate(items):
        item["score"] = st.session_state.get(
            f"{key_prefix}_item_{i}_score", _safe_float(item.get("score"), 0.0)
        )
        item["deduction_reason"] = st.session_state.get(
            f"{key_prefix}_item_{i}_reason", item.get("deduction_reason", "")
        )
        item["comment"] = st.session_state.get(
            f"{key_prefix}_item_{i}_comment", item.get("comment", "")
        )

    grading["overall_comment"] = st.session_state.get(
        f"{key_prefix}_overall_comment", grading.get("overall_comment", "")
    )
    grading["total_score"] = st.session_state.get(
        f"{key_prefix}_total_score", _safe_float(grading.get("total_score"), 0.0)
    )
    data["approved"] = bool(st.session_state.get(f"{key_prefix}_approved", False))
    data["teacher_last_modified"] = datetime.now().isoformat(timespec="seconds")

    current_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

if not results_dir.exists():
    st.warning("Results/ 不存在。请先在左侧点击“拉取并批改作业”。")
    st.stop()

result_files = sorted(results_dir.glob("*.json"))
if not result_files:
    st.warning("Results/ 为空。请先在左侧点击“拉取并批改作业”。")
    st.stop()

if "idx" not in st.session_state:
    st.session_state.idx = 0

col_nav_1, col_nav_2, col_nav_3 = st.columns([1, 2, 1])
with col_nav_1:
    if st.button("⬅ 上一个", use_container_width=True):
        st.session_state.idx = max(0, st.session_state.idx - 1)
with col_nav_3:
    if st.button("下一个 ➡", use_container_width=True):
        st.session_state.idx = min(len(result_files) - 1, st.session_state.idx + 1)
with col_nav_2:
    st.markdown(f"**{st.session_state.idx + 1} / {len(result_files)}**")

current_file = result_files[st.session_state.idx]
data = json.loads(current_file.read_text(encoding="utf-8"))
key_prefix = f"student_{current_file.stem}"

st.subheader(data.get("student_name", "未命名学生"))

left, right = st.columns(2)

with left:
    st.markdown("### 学生作业")
    source_files = [Path(p) for p in data.get("student_source_files", []) if p]
    if not source_files and data.get("student_source_file"):
        source_files = [Path(data["student_source_file"])]

    if not source_files:
        st.warning("未记录到原始附件路径")
    elif len(source_files) == 1:
        _render_source_file(source_files[0])
    else:
        tab_titles = [p.name for p in source_files]
        tabs = st.tabs(tab_titles)
        for tab, src in zip(tabs, source_files):
            with tab:
                _render_source_file(src)

with right:
    st.markdown("### 教师可编辑评分区")
    if data.get("error"):
        st.error(f"评分错误: {data['error']}")
    else:
        grading = data.get("grading", {})
        items = grading.get("items", [])

        calculated_total = sum(
            _safe_float(st.session_state.get(f"{key_prefix}_item_{i}_score", item.get("score", 0)))
            for i, item in enumerate(items)
        )

        col_total_1, col_total_2 = st.columns([2, 1])
        with col_total_1:
            st.number_input(
                "总分（可手动修改）",
                min_value=0.0,
                value=_safe_float(grading.get("total_score"), calculated_total),
                step=0.5,
                key=f"{key_prefix}_total_score",
            )
        with col_total_2:
            if st.button("按各题重算总分", key=f"{key_prefix}_recalc_total", use_container_width=True):
                st.session_state[f"{key_prefix}_total_score"] = calculated_total
                st.success(f"已重算总分: {calculated_total}")

        st.text_area(
            "总评（可编辑）",
            value=grading.get("overall_comment", ""),
            height=100,
            key=f"{key_prefix}_overall_comment",
        )

        for idx, item in enumerate(items, start=1):
            with st.expander(f"题 {item.get('question_no', idx)}"):
                max_score = _safe_float(item.get("max_score"), 100.0)
                init_score = _safe_float(item.get("score"), 0.0)
                st.number_input(
                    f"得分（满分 {item.get('max_score', '-')})",
                    min_value=0.0,
                    max_value=max_score,
                    value=min(init_score, max_score),
                    step=0.5,
                    key=f"{key_prefix}_item_{idx - 1}_score",
                )
                st.text_input(
                    "扣分原因（可编辑）",
                    value=item.get("deduction_reason", ""),
                    key=f"{key_prefix}_item_{idx - 1}_reason",
                )
                st.text_area(
                    "评语（可编辑）",
                    value=item.get("comment", ""),
                    height=80,
                    key=f"{key_prefix}_item_{idx - 1}_comment",
                )

st.checkbox(
    "审核通过，允许回传 Canvas",
    value=bool(data.get("approved", False)),
    key=f"{key_prefix}_approved",
)

col_action_1, col_action_2, col_action_3, col_action_4 = st.columns(4)
with col_action_1:
    if st.button("临时保存教师修改", use_container_width=True):
        _save_current_file(current_file, data, key_prefix)
        st.success("已临时保存当前教师修改")

with col_action_2:
    if st.button("保存并标记审核通过", use_container_width=True):
        st.session_state[f"{key_prefix}_approved"] = True
        _save_current_file(current_file, data, key_prefix)
        st.success("已保存并标记为审核通过")

with col_action_3:
    if st.button("提交当前学生到 Canvas", use_container_width=True):
        _save_current_file(current_file, data, key_prefix)
        ok, message = submit_single_result_file(current_file)
        if ok:
            st.success(message)
        else:
            st.error(message)

with col_action_4:
    if st.button("提交全部已审核结果", use_container_width=True):
        _save_current_file(current_file, data, key_prefix)
        success, skipped, failed = submit_approved_results_with_stats()
        if failed == 0:
            st.success(f"提交完成: 成功 {success} | 跳过 {skipped} | 失败 {failed}")
        else:
            st.error(f"提交完成: 成功 {success} | 跳过 {skipped} | 失败 {failed}")
