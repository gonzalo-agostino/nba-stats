"""Deploy NBA Stats FastAPI backend to a Hugging Face Docker Space and configure secrets."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

from nba_stats.config import Settings

SPACE_README = """---
title: NBA Stats API
emoji: 🏀
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
---

# NBA Stats Analytics API

FastAPI backend for NBA Stats & What-If Simulator (LightGBM + SHAP + DuckDB + The Odds API).
"""


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Uso: python scripts/deploy_hf_space.py <usuario/nombre-space>")

    space_id = sys.argv[1].strip()
    settings = Settings()
    token = (settings.hf_token or "").strip()
    dataset_repo = (settings.hf_dataset_repo or "").strip()

    if not token or not dataset_repo:
        raise SystemExit("Faltan HF_TOKEN y HF_DATASET_REPO en .env")

    api = HfApi(token=token)

    print(f"1/3 Verificando/creando Space Docker '{space_id}'...")
    api.create_repo(
        repo_id=space_id,
        repo_type="space",
        space_sdk="docker",
        private=False,
        exist_ok=True,
    )

    print("2/3 Configurando Secrets en el Space...")
    secrets: dict[str, str] = {
        "HF_TOKEN": token,
        "HF_DATASET_REPO": dataset_repo,
    }
    if settings.the_odds_api_key:
        secrets["THE_ODDS_API_KEY"] = settings.the_odds_api_key
    if settings.google_cloud_project:
        secrets["GOOGLE_CLOUD_PROJECT"] = settings.google_cloud_project
    if settings.google_cloud_location:
        secrets["GOOGLE_CLOUD_LOCATION"] = settings.google_cloud_location
    if settings.vertex_model_name:
        secrets["VERTEX_MODEL_NAME"] = settings.vertex_model_name

    for key, value in secrets.items():
        api.add_space_secret(repo_id=space_id, key=key, value=value)
        print(f"    - Secret configurado: {key}")

    print("3/3 Subiendo código del backend y Dockerfile al Space...")
    root = settings.project_root
    with tempfile.TemporaryDirectory() as tmpdir:
        stage = Path(tmpdir)
        (stage / "README.md").write_text(SPACE_README, encoding="utf-8")
        for fname in ("Dockerfile", ".dockerignore", "pyproject.toml"):
            src_file = root / fname
            if src_file.exists():
                shutil.copy2(src_file, stage / fname)
        for dirname in ("src", "configs"):
            src_dir = root / dirname
            if src_dir.exists():
                shutil.copytree(
                    src_dir,
                    stage / dirname,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".ruff_cache"),
                )

        api.upload_folder(
            folder_path=str(stage),
            repo_id=space_id,
            repo_type="space",
            commit_message="Deploy NBA Stats FastAPI backend",
        )

    owner, name = space_id.split("/", 1)
    space_url = f"https://{owner.lower()}-{name.lower()}.hf.space"
    print(f"\nDeploy enviado con éxito!")
    print(f"Space UI:  https://huggingface.co/spaces/{space_id}")
    print(f"API URL:   {space_url}")
    print(f"Health:    {space_url}/api/v1/health")


if __name__ == "__main__":
    main()
