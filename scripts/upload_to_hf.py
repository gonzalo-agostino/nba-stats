"""One-time helper to upload local DuckDB warehouse & ML models to a private HF Dataset."""

from __future__ import annotations

from nba_stats.config import Settings
from nba_stats.data.hf_sync import HFStorageSync


def main() -> None:
    settings = Settings()
    sync = HFStorageSync(settings=settings)
    if not sync.enabled:
        raise SystemExit(
            "Faltan HF_DATASET_REPO y HF_TOKEN en tu .env (o variables de entorno).\n"
            "Ejemplo:\n"
            "  HF_DATASET_REPO=tu-usuario/nba-stats-data\n"
            "  HF_TOKEN=hf_xxxxxxxxx"
        )
    print(f"Subiendo data/warehouse/nba.duckdb, data/raw/ y artifacts/models/ a '{sync.repo_id}'...")
    sync.push_all()
    print("Listo! Tus datos, modelos y picks ya quedaron guardados en tu Dataset privado de Hugging Face.")


if __name__ == "__main__":
    main()
