"""Environment-driven configuration for Nyanjou.

Every setting has a documented default (see .env.example and
docs/nyanjou-integration.md). Secrets are only read from the environment and
are never written to disk by this module.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Voices accepted by GPT-Live (openai SDK `BuiltInVoice`, Nov 2026).
GPT_LIVE_VOICES = (
    "alloy", "ash", "ballad", "beacon", "bossa", "cedar", "cinder", "coral",
    "delta", "echo", "gleam", "marin", "meridian", "quartz", "ripple", "sage",
    "shida", "shimmer", "stone", "tempo", "verse", "vesper", "willow",
)
GPT_LIVE_MODELS = ("gpt-live-1.1", "gpt-live-1")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _str(name: str, default: str = "", *fallbacks: str) -> str:
    for key in (name, *fallbacks):
        raw = os.getenv(key)
        if raw is not None and raw.strip():
            return raw.strip()
    return default


def _path(name: str, default: str) -> Path:
    p = Path(_str(name, default)).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


@dataclass
class NyanjouConfig:
    # OpenAI / GPT-Live
    openai_api_key: str = ""
    live_url: str = "wss://api.openai.com/v1/live/sessions"
    voice_model: str = "gpt-live-1.1"
    voice_name: str = "marin"
    backend_model: str = "gpt-5.6-luna"
    backend_reasoning_effort: str = "low"
    backend_web_search: bool = True
    assistant_name: str = "Nyanjou"
    language: str = "de"
    extra_instructions: str = ""

    # Discord / authorization
    owner_id: int | None = None
    allowed_users_db: Path = field(default_factory=lambda: REPO_ROOT / "allowed_users.sqlite")
    allow_guest_conversation: bool = True
    speaker_mode: str = "floor"  # "floor" (one speaker at a time) or "mix"
    attribution_window_ms: int = 8000
    attribution_tolerance_ms: int = 600
    floor_release_ms: int = 700
    vad_threshold_dbfs: float = -45.0
    barge_in: bool = True
    barge_in_ms: int = 160
    idle_timeout_s: int = 900
    results_to: str = "channel"  # channel | dm | none

    # Voice bridge
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8765
    bridge_token: str = ""
    bridge_autostart: bool = True
    bridge_node: str = "node"
    bridge_dir: Path = field(default_factory=lambda: REPO_ROOT / "bridge")

    # T3 Code MCP
    t3_mcp_url: str = ""
    t3_project_id: str = ""
    t3_token_file: Path = field(default_factory=lambda: REPO_ROOT / "data" / "t3_oauth.json")
    t3_static_token: str = ""
    t3_workspace_strategy: str = ""  # "", "root" or "worktree"
    t3_base_ref: str = ""
    t3_runtime_mode: str = ""  # empty = inherit T3 default
    t3_allow_scratch: bool = True
    t3_wait_slice_ms: int = 25000

    # Tasks
    tasks_db: Path = field(default_factory=lambda: REPO_ROOT / "data" / "nyanjou_tasks.sqlite")
    max_parallel_tasks: int = 4
    task_timeout_s: int = 7200

    debug: bool = False

    @classmethod
    def from_env(cls) -> "NyanjouConfig":
        owner_raw = _str("NYANJOU_OWNER_ID")
        owner_id = int(owner_raw) if owner_raw.isdigit() else None
        token = _str("NYANJOU_BRIDGE_TOKEN")
        cfg = cls(
            openai_api_key=_str("OPENAI_API_KEY", "", "OPENAI_TOKEN"),
            live_url=_str("NYANJOU_LIVE_URL", cls.live_url),
            voice_model=_str("NYANJOU_VOICE_MODEL", cls.voice_model),
            voice_name=_str("NYANJOU_VOICE_NAME", cls.voice_name),
            backend_model=_str("NYANJOU_BACKEND_MODEL", cls.backend_model),
            backend_reasoning_effort=_str("NYANJOU_BACKEND_REASONING", cls.backend_reasoning_effort),
            backend_web_search=_bool("NYANJOU_BACKEND_WEB_SEARCH", True),
            assistant_name=_str("NYANJOU_NAME", cls.assistant_name),
            language=_str("NYANJOU_LANGUAGE", cls.language),
            extra_instructions=_str("NYANJOU_EXTRA_INSTRUCTIONS"),
            owner_id=owner_id,
            allowed_users_db=_path("NYANJOU_ALLOWED_USERS_DB", "allowed_users.sqlite"),
            allow_guest_conversation=_bool("NYANJOU_ALLOW_GUEST_CONVERSATION", True),
            speaker_mode=_str("NYANJOU_SPEAKER_MODE", cls.speaker_mode).lower(),
            attribution_window_ms=_int("NYANJOU_ATTRIBUTION_WINDOW_MS", cls.attribution_window_ms),
            attribution_tolerance_ms=_int("NYANJOU_ATTRIBUTION_TOLERANCE_MS", cls.attribution_tolerance_ms),
            floor_release_ms=_int("NYANJOU_FLOOR_RELEASE_MS", cls.floor_release_ms),
            vad_threshold_dbfs=_float("NYANJOU_VAD_THRESHOLD_DBFS", cls.vad_threshold_dbfs),
            barge_in=_bool("NYANJOU_BARGE_IN", True),
            barge_in_ms=_int("NYANJOU_BARGE_IN_MS", cls.barge_in_ms),
            idle_timeout_s=_int("NYANJOU_IDLE_TIMEOUT_S", cls.idle_timeout_s),
            results_to=_str("NYANJOU_RESULTS_TO", cls.results_to).lower(),
            bridge_host=_str("NYANJOU_BRIDGE_HOST", cls.bridge_host),
            bridge_port=_int("NYANJOU_BRIDGE_PORT", cls.bridge_port),
            # Without an explicit token, use a fresh random one per process (autostart only).
            bridge_token=token or secrets.token_urlsafe(32),
            bridge_autostart=_bool("NYANJOU_BRIDGE_AUTOSTART", True),
            bridge_node=_str("NYANJOU_BRIDGE_NODE", cls.bridge_node),
            bridge_dir=_path("NYANJOU_BRIDGE_DIR", "bridge"),
            t3_mcp_url=_str("T3_MCP_URL"),
            t3_project_id=_str("T3_PROJECT_ID"),
            t3_token_file=_path("T3_MCP_TOKEN_FILE", "data/t3_oauth.json"),
            t3_static_token=_str("T3_MCP_ACCESS_TOKEN"),
            t3_workspace_strategy=_str("T3_WORKSPACE_STRATEGY").lower(),
            t3_base_ref=_str("T3_BASE_REF"),
            t3_runtime_mode=_str("T3_RUNTIME_MODE"),
            t3_allow_scratch=_bool("T3_ALLOW_SCRATCH", True),
            t3_wait_slice_ms=_int("T3_WAIT_SLICE_MS", cls.t3_wait_slice_ms),
            tasks_db=_path("NYANJOU_TASKS_DB", "data/nyanjou_tasks.sqlite"),
            max_parallel_tasks=max(1, _int("NYANJOU_MAX_PARALLEL_TASKS", cls.max_parallel_tasks)),
            task_timeout_s=_int("NYANJOU_TASK_TIMEOUT_S", cls.task_timeout_s),
            debug=_bool("NYANJOU_DEBUG", False),
        )
        if not token and not cfg.bridge_autostart:
            raise ValueError("NYANJOU_BRIDGE_TOKEN is required when NYANJOU_BRIDGE_AUTOSTART=false")
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.speaker_mode not in {"floor", "mix"}:
            raise ValueError("NYANJOU_SPEAKER_MODE must be 'floor' or 'mix'")
        if self.results_to not in {"channel", "dm", "none"}:
            raise ValueError("NYANJOU_RESULTS_TO must be 'channel', 'dm' or 'none'")
        if self.t3_workspace_strategy not in {"", "root", "worktree"}:
            raise ValueError("T3_WORKSPACE_STRATEGY must be empty, 'root' or 'worktree'")
        if self.t3_mcp_url:
            check_mcp_url(self.t3_mcp_url)

    @property
    def t3_configured(self) -> bool:
        return bool(self.t3_mcp_url)

    @property
    def live_configured(self) -> bool:
        return bool(self.openai_api_key)


def check_mcp_url(url: str) -> None:
    """T3 sends credentials only over https or to localhost; mirror that rule."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    raise ValueError("T3_MCP_URL must use https:// or http://localhost")
