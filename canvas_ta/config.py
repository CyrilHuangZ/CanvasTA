import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str, default: bool = False) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "y", "on"}


def _as_optional_int(value: str) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    return int(raw)


def _as_float(value: str, default: float) -> float:
    raw = (value or "").strip()
    if not raw:
        return default
    return float(raw)


def _load_dotenv_with_library(dotenv_path: Path) -> bool:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return False

    # Use project .env as source of truth to avoid stale shell-level variables.
    load_dotenv(dotenv_path=dotenv_path, override=True, encoding="utf-8")
    return True


def _load_dotenv_file(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def _first_file(path: Path) -> Path:
    files = sorted(p for p in path.glob("*") if p.is_file())
    return files[0] if files else Path("")


def _assignment_answer_file(path: Path, assignment_id: int) -> Path:
    key = str(assignment_id)
    candidates = sorted(
        (p for p in path.glob("*") if p.is_file() and key in p.name),
        key=lambda p: p.name.lower(),
    )
    return candidates[0] if candidates else Path("")


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DOTENV_PATH = _PROJECT_ROOT / ".env"
if not _load_dotenv_with_library(_DOTENV_PATH):
    _load_dotenv_file(_DOTENV_PATH)


@dataclass
class Settings:
    canvas_url: str = os.getenv("CANVAS_URL", "https://canvas.shufe.edu.cn")
    canvas_token: str = os.getenv("CANVAS_TOKEN", "")
    course_id: int = int(os.getenv("COURSE_ID", "39108"))
    assignment_id: int = int(os.getenv("ASSIGNMENT_ID", "35418"))

    llm_provider: str = os.getenv("LLM_PROVIDER", "auto").lower()
    llm_api_url: str = os.getenv("LLM_API_URL", "").strip()
    llm_base_url: str = os.getenv(
        "LLM_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    ).strip()
    llm_api_key: str = os.getenv(
        "LLM_API_KEY",
        os.getenv("OPENAI_API_KEY", os.getenv("AZURE_OPENAI_API_KEY", "")),
    ).strip()
    azure_openai_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    azure_openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01").strip()
    vision_model: str = os.getenv("VISION_MODEL", "qwen2.5-vl-72b-instruct")
    grading_model: str = os.getenv("GRADING_MODEL", "deepseek-v3.1")
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "180"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    llm_retry_backoff_seconds: float = _as_float(
        os.getenv("LLM_RETRY_BACKOFF_SECONDS", "2.0"),
        2.0,
    )
    max_vision_pages: int = int(os.getenv("MAX_VISION_PAGES", "5"))
    vision_image_target_kb: int = int(os.getenv("VISION_IMAGE_TARGET_KB", "200"))
    vision_render_dpi: int = int(os.getenv("VISION_RENDER_DPI", "160"))
    vision_jpeg_quality: int = int(os.getenv("VISION_JPEG_QUALITY", "72"))
    vision_max_width: int = int(os.getenv("VISION_MAX_WIDTH", "1600"))

    root_dir: Path = Path(os.getenv("ROOT_DIR", "."))
    download_dir: Path = Path(os.getenv("DOWNLOAD_DIR", "./student_submissions"))
    results_dir: Path = Path(os.getenv("RESULTS_DIR", "./Results"))
    answer_dir: Path = Path(os.getenv("ANSWER_DIR", "./Answer"))

    deduction_rules: str = os.getenv(
        "DEDUCTION_RULES",
        "请配置扣分细则，例如：总分100分，步骤分，概念错误要明确扣分原因。",
    )
    return_comment_to_canvas: bool = _as_bool(os.getenv("RETURN_COMMENT_TO_CANVAS", "false"))
    total_questions: int | None = _as_optional_int(os.getenv("TOTAL_QUESTIONS", ""))

    @property
    def answer_file(self) -> Path:
        configured = os.getenv("ANSWER_FILE", "").strip()
        if configured:
            configured_path = Path(configured)
            if configured_path.exists():
                return configured_path

        assignment_matched = _assignment_answer_file(self.answer_dir, self.assignment_id)
        if assignment_matched:
            return assignment_matched

        if configured:
            # Keep configured path in error messages when no fallback file is found.
            return Path(configured)

        return _first_file(self.answer_dir)

    @property
    def assignment_tag(self) -> str:
        return f"assignment_{self.assignment_id}"

    @property
    def assignment_download_dir(self) -> Path:
        return self.download_dir / self.assignment_tag

    @property
    def assignment_results_dir(self) -> Path:
        return self.results_dir / self.assignment_tag

    @property
    def assignment_history_dir(self) -> Path:
        return self.assignment_results_dir / "history"

    def ensure_dirs(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.assignment_download_dir.mkdir(parents=True, exist_ok=True)
        self.assignment_results_dir.mkdir(parents=True, exist_ok=True)
        self.assignment_history_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_azure_openai(self) -> bool:
        if self.llm_provider == "azure":
            return True
        if self.llm_provider in {"openai", "custom"}:
            return False
        return bool(self.azure_openai_endpoint)

    @property
    def resolved_llm_api_url(self) -> str:
        if self.llm_api_url:
            return self.llm_api_url

        base = self.llm_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"
