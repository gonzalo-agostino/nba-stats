"""Application configuration loaded from environment variables and YAML files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


class RateLimitConfig(BaseModel):
    min_delay_seconds: float = 0.9
    max_delay_seconds: float = 1.6
    timeout_seconds: int = 45
    max_retries: int = 4
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 30.0


class IngestionConfig(BaseModel):
    default_seasons: list[str] = Field(default_factory=lambda: ["2024-25", "2025-26", "2026-27"])
    season_type: str = "Regular Season"
    active_season: str = "2026-27"
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


class RotationConfig(BaseModel):
    min_avg_minutes: float = 12.0
    star_usg_threshold: float = 0.25
    star_min_threshold: float = 30.0
    min_prior_games_for_training: int = 5


class WowyConfig(BaseModel):
    shrinkage_k: float = 8.0
    max_lookback_games: int = 120


class FeaturesConfig(BaseModel):
    rolling_windows: list[int] = Field(default_factory=lambda: [5, 10, 20])
    ewma_span: int = 10
    rotation: RotationConfig = Field(default_factory=RotationConfig)
    wowy: WowyConfig = Field(default_factory=WowyConfig)


class ValidationConfig(BaseModel):
    n_splits: int = 5
    min_train_fraction: float = 0.45
    min_prior_games: int = 5


class TrainingConfig(BaseModel):
    stage1_target: str = "min"
    stage2_targets: list[str] = Field(
        default_factory=lambda: ["pts", "reb", "ast", "stl", "blk", "tov", "pf", "fga", "ftm"]
    )
    derived_targets: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "pra": ["pts", "reb", "ast"],
            "pr": ["pts", "reb"],
            "pa": ["pts", "ast"],
        }
    )
    target_objectives: dict[str, str] = Field(
        default_factory=lambda: {
            "stl": "poisson",
            "blk": "poisson",
            "tov": "poisson",
            "pf": "poisson",
        }
    )
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    lightgbm: dict[str, Any] = Field(
        default_factory=lambda: {
            "objective": "regression_l1",
            "metric": "mae",
            "learning_rate": 0.03,
            "num_leaves": 31,
            "min_child_samples": 40,
            "subsample": 0.8,
            "subsample_freq": 1,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.5,
            "reg_lambda": 1.0,
            "n_estimators": 800,
            "early_stopping_rounds": 50,
            "random_state": 42,
            "n_jobs": -1,
            "verbose": -1,
        }
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_root: Path = Field(default_factory=_project_root)
    nba_stats_data_dir: Path = Field(default=Path("data"))
    nba_stats_log_level: str = "INFO"

    nba_api_timeout_seconds: int | None = None
    nba_api_min_delay_seconds: float | None = None
    nba_api_max_delay_seconds: float | None = None
    nba_api_max_retries: int | None = None

    # Phase 3: Vertex AI & LangSmith
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    vertex_model_name: str = "gemini-3.8-flash"
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "nba-stats"

    # The Odds API (automated NBA player prop lines & Win Rate tracking)
    the_odds_api_key: str | None = None
    auto_sync_enabled: bool = True

    @property
    def data_dir(self) -> Path:
        if self.nba_stats_data_dir.is_absolute():
            return self.nba_stats_data_dir
        return self.project_root / self.nba_stats_data_dir

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def warehouse_dir(self) -> Path:
        return self.data_dir / "warehouse"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def duckdb_path(self) -> Path:
        return self.warehouse_dir / "nba.duckdb"

    @property
    def configs_dir(self) -> Path:
        return self.project_root / "configs"

    @property
    def artifacts_dir(self) -> Path:
        return self.project_root / "artifacts"

    @property
    def models_dir(self) -> Path:
        return self.artifacts_dir / "models"

    def ensure_dirs(self) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.warehouse_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)


def load_ingestion_config(settings: Settings | None = None) -> IngestionConfig:
    """Load ingestion YAML config and apply optional env overrides."""
    settings = settings or Settings()
    yaml_path = settings.configs_dir / "ingestion.yaml"
    raw_data: dict[str, Any] = {}
    if yaml_path.exists():
        with yaml_path.open("r", encoding="utf-8") as fh:
            raw_data = yaml.safe_load(fh) or {}

    cfg = IngestionConfig.model_validate(raw_data)

    if settings.nba_api_timeout_seconds is not None:
        cfg.rate_limit.timeout_seconds = settings.nba_api_timeout_seconds
    if settings.nba_api_min_delay_seconds is not None:
        cfg.rate_limit.min_delay_seconds = settings.nba_api_min_delay_seconds
    if settings.nba_api_max_delay_seconds is not None:
        cfg.rate_limit.max_delay_seconds = settings.nba_api_max_delay_seconds
    if settings.nba_api_max_retries is not None:
        cfg.rate_limit.max_retries = settings.nba_api_max_retries

    return cfg


def load_features_config(settings: Settings | None = None) -> FeaturesConfig:
    """Load feature engineering YAML config."""
    settings = settings or Settings()
    yaml_path = settings.configs_dir / "features.yaml"
    raw_data: dict[str, Any] = {}
    if yaml_path.exists():
        with yaml_path.open("r", encoding="utf-8") as fh:
            raw_data = yaml.safe_load(fh) or {}
    return FeaturesConfig.model_validate(raw_data)


def load_training_config(settings: Settings | None = None) -> TrainingConfig:
    """Load ML training YAML config."""
    settings = settings or Settings()
    yaml_path = settings.configs_dir / "training.yaml"
    raw_data: dict[str, Any] = {}
    if yaml_path.exists():
        with yaml_path.open("r", encoding="utf-8") as fh:
            raw_data = yaml.safe_load(fh) or {}
    return TrainingConfig.model_validate(raw_data)
