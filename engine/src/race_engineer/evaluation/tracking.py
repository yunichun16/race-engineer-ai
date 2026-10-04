"""MLflow experiment tracking, stored locally in the repo (mlruns.db + mlartifacts/, gitignored).

Browse runs with:  uv run mlflow ui --backend-store-uri sqlite:///../mlruns.db
"""

from __future__ import annotations

import os
from typing import Any

from race_engineer.config import repo_root


def mlflow_experiment(name: str) -> Any:
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow

    mlflow.set_tracking_uri(f"sqlite:///{repo_root() / 'mlruns.db'}")
    if mlflow.get_experiment_by_name(name) is None:
        mlflow.create_experiment(
            name, artifact_location=(repo_root() / "mlartifacts" / name).as_uri()
        )
    mlflow.set_experiment(name)
    return mlflow
