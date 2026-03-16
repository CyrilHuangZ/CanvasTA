from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import streamlit as st

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
results_dir = settings.results_dir
project_root = Path(__file__).resolve().parent.parent
logo_path = project_root / "Logo" / "CanvasTA.png"

if logo_path.exists():
    st.image(str(logo_path), width=180)

st.title("CanvasTA 一体化工作台")
st.caption("支持一站式完成批改、审阅、审核标记与回传。")
# 主操作界面已移除顶部声明，免责声明保留在侧边栏以避免干扰主操作流程

with st.sidebar:
    st.header("流程控制")
    if st.button("1) 拉取并批改作业", use_container_width=True):
        with st.spinner("正在批改中，这可能需要几分钟..."):
            run_grading_pipeline()
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
    st.write(f"来源文件: {data.get('student_source_file', '未知')}")
    st.text_area(
        "提取文本",
        value=data.get("student_answer_text", "(无提取文本或提取失败)"),
        height=520,
        key=f"{key_prefix}_student_text_view",
        disabled=True,
    )

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
