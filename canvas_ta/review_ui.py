from __future__ import annotations

import json
import shutil
from datetime import datetime
from io import BytesIO
from functools import lru_cache
from pathlib import Path
import re

import streamlit as st

try:
    import fitz  # type: ignore
except Exception:
    fitz = None

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


def _safe_reset_download_dir(target_dir: Path) -> tuple[bool, str]:
    target_dir = target_dir.resolve()
    parent = target_dir.parent
    parent.mkdir(parents=True, exist_ok=True)

    if not target_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
        return True, f"下载目录不存在，已创建: {target_dir}"

    try:
        shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        return True, f"已重置下载目录: {target_dir}"
    except PermissionError:
        # Windows 下若目录被占用，采用改名隔离避免页面崩溃。
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantined = parent / f"{target_dir.name}__locked_{stamp}"
        try:
            target_dir.rename(quarantined)
            target_dir.mkdir(parents=True, exist_ok=True)
            return True, f"目录占用，已隔离为: {quarantined.name}；并创建新目录: {target_dir.name}"
        except Exception as exc:
            return False, (
                f"重置失败（目录被占用）: {target_dir}。"
                f"请关闭资源管理器预览/编辑器占用后重试。详细信息: {exc}"
            )
    except Exception as exc:
        return False, f"重置下载目录失败: {exc}"

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

    retry_failed_only = st.checkbox(
        "仅重试上次可重试错误（超时/限流等）",
        value=False,
        help="开启后只会重跑上次标记 needs_retry=true 的学生。",
    )
    include_approved = st.checkbox(
        "重跑已审核学生（谨慎）",
        value=False,
        help="默认会跳过已审核学生，避免覆盖人工审核结果。",
    )
    reuse_valid_results = st.checkbox(
        "复用已有正常结果（仅下载不重批）",
        value=True,
        help="若已有 JSON 且无 error 并含 total_score，则只下载附件，不重新调用模型批改。",
    )
    reset_download_dir = st.checkbox(
        "批改前清空本作业下载目录",
        value=False,
        help="仅清空 student_submissions/assignment_xxx，不影响 Results 与历史批改记录。",
    )

    st.divider()
    st.subheader("标准答案显示")
    standard_window_height = st.slider(
        "标准答案滑窗高度",
        min_value=120,
        max_value=720,
        value=320,
        step=20,
        help="每题标准答案仅保留一个滚动窗口，内容完整渲染。",
    )
    student_window_height = st.slider(
        "学生作业滑窗高度",
        min_value=240,
        max_value=900,
        value=520,
        step=20,
        help="学生附件预览区采用固定高度滚动窗口，避免页面过长。",
    )

    progress_text_slot = st.empty()
    progress_bar_slot = st.progress(0)

    can_start_grading = total_questions_input > 0 or allow_run_with_zero
    if st.button("1) 拉取并批改作业", use_container_width=True, disabled=not can_start_grading):
        if reset_download_dir:
            target_dir = settings.assignment_download_dir
            ok, msg = _safe_reset_download_dir(target_dir)
            if ok:
                st.info(msg)
            else:
                st.error(msg)
                st.stop()

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
                retry_failed_only=retry_failed_only,
                skip_approved=not include_approved,
                reuse_valid_results=reuse_valid_results,
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


def _clamp_score(value: float, *, max_score: float) -> float:
    upper = max(0.0, max_score)
    return min(max(0.0, value), upper)


def _score_ratio(score: float, max_score: float) -> float:
    if max_score <= 0:
        return 0.0
    return max(0.0, min(1.0, score / max_score))


def _interpolate_color_hex(ratio: float) -> str:
    # Pleasant palette: low=#e76f51, mid=#e9c46a, high=#2a9d8f
    low = (231, 111, 81)
    mid = (233, 196, 106)
    high = (42, 157, 143)

    if ratio <= 0.5:
        t = ratio / 0.5
        start, end = low, mid
    else:
        t = (ratio - 0.5) / 0.5
        start, end = mid, high

    r = int(start[0] + (end[0] - start[0]) * t)
    g = int(start[1] + (end[1] - start[1]) * t)
    b = int(start[2] + (end[2] - start[2]) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _render_score_bar(score: float, max_score: float) -> None:
    ratio = _score_ratio(score, max_score)
    width = int(ratio * 100)
    bar_color = _interpolate_color_hex(ratio)
    st.markdown(
        f"""
<div style="margin: 2px 0 10px 0;">
  <div style="display:flex; justify-content:space-between; font-size:12px; color:#5b5f66;">
    <span>得分热度</span>
    <span>{width}%</span>
  </div>
  <div style="height:10px; background:#eef1f5; border-radius:999px; overflow:hidden;">
    <div style="height:100%; width:{width}%; background:{bar_color}; transition:width .25s ease;"></div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def _heat_badge_text(score: float, max_score: float) -> str:
    ratio = _score_ratio(score, max_score)
    pct = int(ratio * 100)
    if pct >= 85:
        icon = "🟢"
    elif pct >= 70:
        icon = "🟡"
    elif pct >= 50:
        icon = "🟠"
    else:
        icon = "🔴"
    return f"{icon} {pct}%"


@lru_cache(maxsize=1)
def _load_standard_answer_text() -> str:
    answer_file = settings.answer_file
    if not answer_file.exists() or not answer_file.is_file():
        return ""

    suffix = answer_file.suffix.lower()
    if suffix in {".txt", ".md", ".tex"}:
        try:
            return answer_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return answer_file.read_text(encoding="gb18030", errors="ignore")
    return ""


@lru_cache(maxsize=1)
def _standard_answer_map() -> dict[str, str]:
    text = _load_standard_answer_text().strip()
    if not text:
        return {}

    normalized = text.replace("\r\n", "\n")
    lines = normalized.split("\n")
    headers: list[tuple[str, int]] = []

    markdown_q = re.compile(r"^\s*#{1,6}\s*([0-9]+(?:\.[0-9]+)*)\b")
    chinese_q = re.compile(r"^\s*题\s*([0-9]+(?:\.[0-9]+)*)\s*[：:.、．]?")

    for i, line in enumerate(lines):
        m1 = markdown_q.match(line)
        if m1:
            headers.append((m1.group(1), i))
            continue
        m2 = chinese_q.match(line)
        if m2:
            headers.append((m2.group(1), i))

    if not headers:
        return {}

    result: dict[str, str] = {}
    for i, (q_no, line_idx) in enumerate(headers):
        if not q_no:
            continue
        start_line = line_idx + 1
        end_line = headers[i + 1][1] if i + 1 < len(headers) else len(lines)
        chunk = "\n".join(lines[start_line:end_line]).strip()
        if chunk:
            result[q_no] = chunk
    return result


def _normalize_markdown_math_blocks(text: str) -> str:
    # Convert legacy block math markers `[` ... `]` into streamlit-friendly `$$` blocks.
    output: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped == "[":
            output.append("$$")
        elif stripped == "]":
            output.append("$$")
        else:
            output.append(line)
    return "\n".join(output)


def _render_standard_answer_window(text: str, height: int) -> None:
    rendered = _normalize_markdown_math_blocks(text)
    try:
        with st.container(height=height, border=True):
            st.markdown(rendered)
    except TypeError:
        # Fallback for older Streamlit versions without `height` in container.
        st.markdown(rendered)


def _render_scroll_window(height: int, renderer) -> None:
    try:
        with st.container(height=height, border=True):
            renderer()
    except TypeError:
        renderer()


def _resolve_standard_answer_for_item(item: dict, item_idx: int | None = None) -> str:
    direct = str(item.get("standard_answer", "") or "").strip()
    if direct:
        return direct

    q_no = str(item.get("question_no", "") or "").strip()
    mapping = _standard_answer_map()

    if q_no:
        exact = mapping.get(q_no, "")
        if exact:
            return exact

        # Common mismatch: grader returns 1..8, standard answer uses 2.1..2.8.
        for key, value in mapping.items():
            if key.split(".")[-1] == q_no:
                return value

    if item_idx is not None and 0 <= item_idx < len(mapping):
        ordered_keys = list(mapping.keys())
        return mapping.get(ordered_keys[item_idx], "")

    return ""


def _ensure_grading_template(data: dict) -> dict:
    grading = data.setdefault("grading", {})
    items = grading.get("items")
    if not isinstance(items, list) or not items:
        grading["items"] = [
            {
                "question_no": "1",
                "score": 0,
                "max_score": 100,
                "standard_answer": "",
                "deduction_reason": "",
                "comment": "",
            }
        ]

    if "overall_comment" not in grading:
        grading["overall_comment"] = ""

    if "total_score" not in grading:
        grading["total_score"] = sum(_safe_float(item.get("score"), 0.0) for item in grading["items"])

    return grading


def _render_pdf(file_path: Path) -> None:
    if fitz is None:
        st.info("当前环境缺少 PyMuPDF，无法内嵌预览 PDF。可先下载后本地查看。")
        st.download_button("下载原始 PDF", data=file_path.read_bytes(), file_name=file_path.name)
        return

    try:
        with fitz.open(file_path) as doc:
            total_pages = len(doc)
            if total_pages == 0:
                st.warning("该 PDF 没有可预览页面。")
                return

            max_preview_pages = 20
            pages_to_show = min(total_pages, max_preview_pages)
            if total_pages > max_preview_pages:
                st.caption(f"PDF 共 {total_pages} 页，当前仅预览前 {max_preview_pages} 页。")

            for page_index in range(pages_to_show):
                page = doc[page_index]
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                st.image(pix.tobytes("png"), caption=f"第 {page_index + 1} 页", use_container_width=True)

    except Exception as exc:
        st.warning(f"PDF 预览失败：{exc}")
        st.download_button("下载原始 PDF", data=file_path.read_bytes(), file_name=file_path.name)


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


def _student_status_text(data: dict) -> str:
    status = str(data.get("canvas_submit_status", "")).strip().lower()
    if status == "success" or data.get("canvas_submitted_at"):
        return "📤已回传"
    if bool(data.get("approved", False)):
        return "✅已审核"
    if data.get("error"):
        return "⚠️待处理"
    return "📝待审核"


def _save_current_file(
    current_path: Path,
    data: dict,
    key_prefix: str,
    *,
    force_approved: bool = False,
) -> None:
    grading = _ensure_grading_template(data)
    items = grading.get("items", [])

    for i, item in enumerate(items):
        score_key = f"{key_prefix}_item_{i}_score"
        max_score = max(0.0, _safe_float(item.get("max_score"), 100.0))
        raw_score = _safe_float(
            st.session_state.get(score_key, _safe_float(item.get("score"), 0.0)),
            0.0,
        )
        item["score"] = _clamp_score(raw_score, max_score=max_score)
        item["deduction_reason"] = st.session_state.get(
            f"{key_prefix}_item_{i}_reason", item.get("deduction_reason", "")
        )
        item["comment"] = st.session_state.get(
            f"{key_prefix}_item_{i}_comment", item.get("comment", "")
        )

    grading["overall_comment"] = st.session_state.get(
        f"{key_prefix}_overall_comment", grading.get("overall_comment", "")
    )
    total_key = f"{key_prefix}_total_score"
    total_raw = _safe_float(
        st.session_state.get(total_key, _safe_float(grading.get("total_score"), 0.0)),
        0.0,
    )
    grading["total_score"] = max(0.0, total_raw)

    if data.get("error"):
        data["manual_review_override"] = True

    approved_from_widget = bool(st.session_state.get(f"{key_prefix}_approved", False))
    data["approved"] = True if force_approved else approved_from_widget
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

student_names = [p.stem for p in result_files]
student_status_map: dict[str, str] = {}
for file_path in result_files:
    try:
        result_data = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        result_data = {}
    student_status_map[file_path.stem] = _student_status_text(result_data)

current_idx = min(max(int(st.session_state.idx), 0), len(result_files) - 1)
if (
    "quick_student_selector" not in st.session_state
    or st.session_state.quick_student_selector not in student_names
):
    st.session_state.quick_student_selector = student_names[current_idx]

col_nav_1, col_nav_2, col_nav_3 = st.columns([1, 2, 1])
with col_nav_1:
    if st.button("⬅ 上一个", use_container_width=True):
        st.session_state.idx = max(0, st.session_state.idx - 1)
        st.session_state.quick_student_selector = student_names[st.session_state.idx]
        st.rerun()
with col_nav_3:
    if st.button("下一个 ➡", use_container_width=True):
        st.session_state.idx = min(len(result_files) - 1, st.session_state.idx + 1)
        st.session_state.quick_student_selector = student_names[st.session_state.idx]
        st.rerun()
with col_nav_2:
    st.markdown(f"**{st.session_state.idx + 1} / {len(result_files)}**")

selected_name = st.selectbox(
    "快速定位学生",
    options=student_names,
    format_func=lambda name: f"{name}  [{student_status_map.get(name, '📝待审核')}]",
    key="quick_student_selector",
    help="可输入姓名快速筛选并跳转。",
)
selected_idx = student_names.index(selected_name)
if selected_idx != st.session_state.idx:
    st.session_state.idx = selected_idx
    st.rerun()

current_file = result_files[st.session_state.idx]
data = json.loads(current_file.read_text(encoding="utf-8"))
key_prefix = f"student_{current_file.stem}"

st.subheader(data.get("student_name", "未命名学生"))

left, right = st.columns(2)

with left:
    st.markdown("### 学生作业")
    source_files = [Path(p) for p in data.get("parsed_source_files", []) if p]
    if not source_files:
        source_files = [Path(p) for p in data.get("student_source_files", []) if p]
    if not source_files and data.get("student_source_file"):
        source_files = [Path(data["student_source_file"])]

    if not source_files:
        st.warning("未记录到原始附件路径")
    elif len(source_files) == 1:
        _render_scroll_window(student_window_height, lambda: _render_source_file(source_files[0]))
    else:
        tab_titles = [p.name for p in source_files]
        tabs = st.tabs(tab_titles)
        for tab, src in zip(tabs, source_files):
            with tab:
                _render_scroll_window(
                    student_window_height,
                    lambda src=src: _render_source_file(src),
                )

with right:
    st.markdown("### 教师可编辑评分区")
    grading = _ensure_grading_template(data)

    if data.get("error"):
        st.error(f"评分错误: {data['error']}")
        if data.get("needs_retry", False):
            st.warning("该错误可重试：建议勾选左侧“仅重试上次可重试错误”后再次批改。")

        st.info("你仍可在下方手动打分并填写评语；保存并审核通过后可以正常提交到 Canvas。")

    items = grading.get("items", [])

    calculated_total = sum(
        _clamp_score(
            _safe_float(st.session_state.get(f"{key_prefix}_item_{i}_score", item.get("score", 0))),
            max_score=max(0.0, _safe_float(item.get("max_score"), 100.0)),
        )
        for i, item in enumerate(items)
    )
    total_key = f"{key_prefix}_total_score"
    total_override_key = f"{key_prefix}_total_score_override"
    total_init = max(0.0, _safe_float(grading.get("total_score"), calculated_total))
    if total_override_key in st.session_state:
        st.session_state[total_key] = max(
            0.0,
            _safe_float(st.session_state.pop(total_override_key), calculated_total),
        )
    if total_key in st.session_state:
        st.session_state[total_key] = max(0.0, _safe_float(st.session_state.get(total_key), total_init))

    col_total_1, col_total_2 = st.columns([2, 1])
    with col_total_1:
        st.number_input(
            "总分（可手动修改）",
            min_value=0.0,
            value=total_init,
            step=0.5,
            key=total_key,
        )
    with col_total_2:
        if st.button("按各题重算总分", key=f"{key_prefix}_recalc_total", use_container_width=True):
            st.session_state[total_override_key] = max(0.0, calculated_total)
            st.rerun()

    st.text_area(
        "总评（可编辑）",
        value=grading.get("overall_comment", ""),
        height=100,
        key=f"{key_prefix}_overall_comment",
    )

    for idx, item in enumerate(items, start=1):
        max_score = max(0.0, _safe_float(item.get("max_score"), 100.0))
        init_score = _clamp_score(_safe_float(item.get("score"), 0.0), max_score=max_score)
        score_key = f"{key_prefix}_item_{idx - 1}_score"
        current_score = _clamp_score(
            _safe_float(st.session_state.get(score_key, init_score), init_score),
            max_score=max_score,
        )
        if score_key in st.session_state:
            st.session_state[score_key] = current_score
        heat = _heat_badge_text(current_score, max_score)
        with st.expander(f"题 {item.get('question_no', idx)}  |  {current_score:g}/{max_score:g}  |  {heat}"):
            st.number_input(
                f"得分（满分 {item.get('max_score', '-')})",
                min_value=0.0,
                max_value=max_score,
                value=current_score,
                step=0.5,
                key=score_key,
            )
            current_score = _clamp_score(
                _safe_float(st.session_state.get(score_key, current_score), current_score),
                max_score=max_score,
            )
            _render_score_bar(current_score, max_score)
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

            standard_answer = _resolve_standard_answer_for_item(item, idx - 1)
            st.markdown("---")
            if standard_answer:
                st.markdown("**标准答案**")
                _render_standard_answer_window(standard_answer, standard_window_height)
            else:
                st.caption("该题暂无可展示的标准答案（请确认标准答案文件中包含可解析题号）。")

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
        _save_current_file(current_file, data, key_prefix, force_approved=True)
        st.success("已保存并标记为审核通过")
        st.rerun()

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
