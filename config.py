from __future__ import annotations

"""Environment-backed settings for the forecasting validator."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional, Tuple


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _first_str(*names: str, default: str = "") -> str:
    for name in names:
        value = _str(name)
        if value:
            return value
    return default


def _optional_str(name: str) -> Optional[str]:
    value = _str(name)
    return value or None


def _int(name: str, default: int) -> int:
    raw = _str(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = _str(name, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a float, got {raw!r}") from exc


def _first_int(*names: str, default: int) -> int:
    raw = _first_str(*names, default=str(default))
    try:
        return int(raw)
    except ValueError as exc:
        joined = " / ".join(names)
        raise ValueError(f"{joined} must be an integer, got {raw!r}") from exc


def _first_float(*names: str, default: float) -> float:
    raw = _first_str(*names, default=str(default))
    try:
        return float(raw)
    except ValueError as exc:
        joined = " / ".join(names)
        raise ValueError(f"{joined} must be a float, got {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = _str(name, "true" if default else "false").lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


def _csv(name: str, default: str) -> Tuple[str, ...]:
    return tuple(part.strip() for part in _str(name, default).split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    task_source: str

    bt_forecast_base_url: str
    bt_forecast_api_key: str
    bt_forecast_api_secret: str
    bt_forecast_page_size: int
    bt_forecast_poll_interval_seconds: int
    bt_forecast_max_concurrent_requests: int
    bt_forecast_request_timeout_seconds: float
    bt_forecast_max_retries: int

    llm_provider: str
    gemini_api_key: Optional[str]
    gemini_model: str
    llm_tasks_per_batch: int
    llm_task_category_mix: Tuple[str, ...]

    db_path: str

    validator_uid: Optional[int]
    validator_hotkey: Optional[str]
    netuid: int
    subtensor_network: str
    wallet_name: str
    wallet_hotkey: str
    dry_run_weights: bool

    simulate_miners: bool
    miner_registry_path: str
    miner_query_timeout_seconds: float
    miner_max_concurrent_queries: int
    miner_query_interval_seconds: int

    period_check_interval_seconds: int
    resolution_interval_seconds: int
    weight_set_interval_seconds: int

    manual_resolution_path: str

    ema_alpha: float
    calibration_error_threshold: float
    calibration_penalty_factor: float
    weight_skill: float
    weight_recent_skill: float
    weight_valid_rate: float
    weight_participation: float


def load_settings() -> Settings:
    _load_dotenv()

    validator_uid_raw = _optional_str("VALIDATOR_UID")
    validator_uid = int(validator_uid_raw) if validator_uid_raw is not None else None
    task_source = _str("TASK_SOURCE", "bt_forecast").lower()
    if task_source == "privatebt":
        task_source = "bt_forecast"

    settings = Settings(
        task_source=task_source,
        bt_forecast_base_url=_first_str("BT_FORECAST_BASE_URL", "PRIVATEBT_BASE_URL"),
        bt_forecast_api_key=_first_str("BT_FORECAST_API_KEY", "PRIVATEBT_API_KEY"),
        bt_forecast_api_secret=_first_str("BT_FORECAST_API_SECRET", "PRIVATEBT_API_SECRET"),
        bt_forecast_page_size=_first_int("BT_FORECAST_PAGE_SIZE", "PRIVATEBT_PAGE_SIZE", default=100),
        bt_forecast_poll_interval_seconds=_first_int(
            "BT_FORECAST_POLL_INTERVAL_SECONDS",
            "PRIVATEBT_POLL_INTERVAL_SECONDS",
            default=30,
        ),
        bt_forecast_max_concurrent_requests=_first_int(
            "BT_FORECAST_MAX_CONCURRENT_REQUESTS",
            "PRIVATEBT_MAX_CONCURRENT_REQUESTS",
            default=4,
        ),
        bt_forecast_request_timeout_seconds=_first_float(
            "BT_FORECAST_TIMEOUT",
            "PRIVATEBT_REQUEST_TIMEOUT_SECONDS",
            default=10,
        ),
        bt_forecast_max_retries=_first_int(
            "BT_FORECAST_MAX_RETRIES",
            "PRIVATEBT_MAX_RETRIES",
            default=3,
        ),
        llm_provider=_str("LLM_PROVIDER", "gemini").lower(),
        gemini_api_key=_optional_str("GEMINI_API_KEY"),
        gemini_model=_str("GEMINI_MODEL", "gemini-2.5-pro"),
        llm_tasks_per_batch=_int("LLM_TASKS_PER_BATCH", 20),
        llm_task_category_mix=_csv(
            "LLM_TASK_CATEGORY_MIX",
            "bittensor,subnets,tao,validators,miners,governance",
        ),
        db_path=_str("DB_PATH", "./data/subnet.db"),
        validator_uid=validator_uid,
        validator_hotkey=_optional_str("VALIDATOR_HOTKEY"),
        netuid=_int("NETUID", 1),
        subtensor_network=_str("SUBTENSOR_NETWORK", "finney"),
        wallet_name=_str("WALLET_NAME", "default"),
        wallet_hotkey=_str("WALLET_HOTKEY", "default"),
        dry_run_weights=_bool("DRY_RUN_WEIGHTS", True),
        simulate_miners=_bool("SIMULATE_MINERS", True),
        miner_registry_path=_str("MINER_REGISTRY_PATH", "./miners.json"),
        miner_query_timeout_seconds=_float("MINER_QUERY_TIMEOUT_SECONDS", 12),
        miner_max_concurrent_queries=_int("MINER_MAX_CONCURRENT_QUERIES", 50),
        miner_query_interval_seconds=_int("MINER_QUERY_INTERVAL_SECONDS", 15),
        period_check_interval_seconds=_int("PERIOD_CHECK_INTERVAL_SECONDS", 15),
        resolution_interval_seconds=_int("RESOLUTION_INTERVAL_SECONDS", 30),
        weight_set_interval_seconds=_int("WEIGHT_SET_INTERVAL_SECONDS", 300),
        manual_resolution_path=_str("MANUAL_RESOLUTION_PATH", "./data/manual_resolutions.json"),
        ema_alpha=_float("EMA_ALPHA", 0.2),
        calibration_error_threshold=_float("CALIBRATION_ERROR_THRESHOLD", 0.2),
        calibration_penalty_factor=_float("CALIBRATION_PENALTY_FACTOR", 0.5),
        weight_skill=_float("WEIGHT_SKILL", 0.55),
        weight_recent_skill=_float("WEIGHT_RECENT_SKILL", 0.25),
        weight_valid_rate=_float("WEIGHT_VALID_RATE", 0.10),
        weight_participation=_float("WEIGHT_PARTICIPATION", 0.10),
    )
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    if settings.task_source not in {"bt_forecast", "llm"}:
        raise ValueError("TASK_SOURCE must be bt_forecast or llm")
    if settings.task_source == "bt_forecast":
        missing = [
            name
            for name, value in (
                ("BT_FORECAST_BASE_URL", settings.bt_forecast_base_url),
                ("BT_FORECAST_API_KEY", settings.bt_forecast_api_key),
                ("BT_FORECAST_API_SECRET", settings.bt_forecast_api_secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Missing required BT-Forecast settings for TASK_SOURCE=bt_forecast: "
                + ", ".join(missing)
            )
    if settings.task_source == "llm":
        if settings.llm_provider != "gemini":
            raise ValueError("LLM_PROVIDER must be gemini")
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for TASK_SOURCE=llm")

    if settings.bt_forecast_page_size < 1 or settings.bt_forecast_page_size > 500:
        raise ValueError("BT_FORECAST_PAGE_SIZE must be between 1 and 500")
    if settings.bt_forecast_max_concurrent_requests < 1:
        raise ValueError("BT_FORECAST_MAX_CONCURRENT_REQUESTS must be at least 1")
    if settings.miner_max_concurrent_queries < 1:
        raise ValueError("MINER_MAX_CONCURRENT_QUERIES must be at least 1")
    if not 0.0 < settings.ema_alpha <= 1.0:
        raise ValueError("EMA_ALPHA must be in (0, 1]")
    if not settings.dry_run_weights and settings.netuid < 0:
        raise ValueError("NETUID must be non-negative when DRY_RUN_WEIGHTS=false")

    score_weight_sum = (
        settings.weight_skill
        + settings.weight_recent_skill
        + settings.weight_valid_rate
        + settings.weight_participation
    )
    if abs(score_weight_sum - 1.0) > 1e-6:
        raise ValueError(
            "WEIGHT_SKILL + WEIGHT_RECENT_SKILL + WEIGHT_VALID_RATE + "
            f"WEIGHT_PARTICIPATION must sum to 1.0, got {score_weight_sum}"
        )
