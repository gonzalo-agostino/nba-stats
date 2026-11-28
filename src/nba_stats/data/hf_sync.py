"""Hugging Face Dataset persistence for DuckDB warehouse and ML artifacts."""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from pathlib import Path

from nba_stats.config import Settings

logger = logging.getLogger(__name__)


class HFStorageSync:
    """Syncs local DuckDB and model artifacts with a private Hugging Face Dataset repo.

    When `HF_DATASET_REPO` and `HF_TOKEN` are configured (e.g. on Hugging Face Spaces),
    this pulls the latest database & models on container startup and pushes `nba.duckdb`
    after mutations (`/sync`, `/props/snapshot`, `/props/grade`) so Win Rate & picks
    survive container restarts.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repo_id = (settings.hf_dataset_repo or "").strip() or None
        self.token = (settings.hf_token or "").strip() or None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.repo_id and self.token)

    def _get_api(self):
        from huggingface_hub import HfApi

        return HfApi(token=self.token)

    def pull_on_startup(self) -> bool:
        """Download warehouse DB and model artifacts from HF Dataset if configured."""
        if not self.enabled:
            return False

        from huggingface_hub import snapshot_download

        self.settings.ensure_dirs()
        logger.info("HFStorageSync: pulling data & artifacts from dataset '%s'...", self.repo_id)
        try:
            with self._lock:
                snapshot_download(
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    token=self.token,
                    local_dir=str(self.settings.project_root),
                    allow_patterns=[
                        "data/warehouse/nba.duckdb",
                        "data/raw/**",
                        "artifacts/models/**",
                    ],
                )
            logger.info("HFStorageSync: pull completed successfully.")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("HFStorageSync: failed pulling from '%s': %s", self.repo_id, exc)
            return False

    def push_db(self, commit_message: str = "Sync nba.duckdb") -> bool:
        """Upload a clean snapshot of `nba.duckdb` to the HF Dataset repo."""
        if not self.enabled:
            return False
        db_path = self.settings.duckdb_path
        if not db_path.exists():
            return False

        try:
            # Copy to a temp file so DuckDB isn't modified mid-upload
            with self._lock, tempfile.TemporaryDirectory() as tmpdir:
                snap_path = Path(tmpdir) / "nba.duckdb"
                shutil.copy2(db_path, snap_path)
                api = self._get_api()
                api.upload_file(
                    path_or_fileobj=str(snap_path),
                    path_in_repo="data/warehouse/nba.duckdb",
                    repo_id=self.repo_id,
                    repo_type="dataset",
                    commit_message=commit_message,
                )
            logger.info("HFStorageSync: pushed nba.duckdb (%s).", commit_message)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("HFStorageSync: failed pushing nba.duckdb: %s", exc)
            return False

    def push_all(self, commit_message: str = "Upload initial NBA Stats data & models") -> bool:
        """Upload `data/warehouse/nba.duckdb`, `data/raw/`, and `artifacts/models/` to HF Dataset."""
        if not self.enabled:
            raise ValueError(
                "Configurá HF_DATASET_REPO y HF_TOKEN en tu .env o variables de entorno."
            )

        api = self._get_api()
        api.create_repo(
            repo_id=self.repo_id,
            repo_type="dataset",
            private=True,
            exist_ok=True,
        )

        uploaded = False
        if self.settings.duckdb_path.exists():
            self.push_db(commit_message=f"{commit_message} (nba.duckdb)")
            uploaded = True

        if self.settings.models_dir.exists():
            api.upload_folder(
                folder_path=str(self.settings.models_dir),
                path_in_repo="artifacts/models",
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"{commit_message} (models)",
            )
            uploaded = True

        if self.settings.raw_dir.exists():
            api.upload_folder(
                folder_path=str(self.settings.raw_dir),
                path_in_repo="data/raw",
                repo_id=self.repo_id,
                repo_type="dataset",
                commit_message=f"{commit_message} (raw)",
            )
            uploaded = True

        return uploaded
