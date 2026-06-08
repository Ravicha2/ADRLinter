from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env from project root before any tests run
_project_root = Path(__file__).resolve().parents[2]
load_dotenv(_project_root / ".env")


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a small Python package tree for deterministic testing.

    Structure:
      sample_repo/
        app/
          __init__.py              -> module "app"
          config.py                -> module "app.config"
          models/
            __init__.py            -> module "app.models"
            user.py                -> module "app.models.user"
            base.py                -> module "app.models.base"
          services/
            __init__.py            -> module "app.services"
            user_service.py        -> module "app.services.user_service"
    """
    app_dir = tmp_path / "app"
    models_dir = app_dir / "models"
    services_dir = app_dir / "services"

    app_dir.mkdir()
    models_dir.mkdir()
    services_dir.mkdir()

    # app/__init__.py - imports from internal modules
    (app_dir / "__init__.py").write_text(
        "from app.config import DEBUG\n"
        "from app.models.user import User\n"
    )

    # app/config.py - top-level assignments only
    (app_dir / "config.py").write_text(
        "DEBUG = True\n"
        "SECRET_KEY = 'dev'\n"
    )

    # app/models/__init__.py - empty
    (models_dir / "__init__.py").write_text("")

    # app/models/base.py - a base class for testing INHERITS
    (models_dir / "base.py").write_text(
        "class BaseModel:\n"
        "    def save(self) -> None:\n"
        "        pass\n"
        "\n"
        "    def delete(self) -> None:\n"
        "        pass\n"
    )

    # app/models/user.py - class with methods, inherits from BaseModel
    (models_dir / "user.py").write_text(
        "from app.models.base import BaseModel\n"
        "\n"
        "class User(BaseModel):\n"
        "    @staticmethod\n"
        "    def find(user_id: int) -> dict:\n"
        "        return {}\n"
        "\n"
        "    @staticmethod\n"
        "    def all() -> list:\n"
        "        return []\n"
    )

    # app/services/__init__.py - empty
    (services_dir / "__init__.py").write_text("")

    # app/services/user_service.py - top-level function that calls User.find
    (services_dir / "user_service.py").write_text(
        "from app.models.user import User\n"
        "\n"
        "def get_user(user_id: int) -> dict:\n"
        "    return User.find(user_id)\n"
    )

    return tmp_path


@pytest.fixture
def flask_repo() -> Path:
    """Path to the flask sample repository in repos/."""
    return Path(__file__).resolve().parents[2] / "repos" / "flask"


# ---------------------------------------------------------------------------
# Diff Processor fixtures (in-memory, no git dependency)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_user_model_source() -> bytes:
    """Python source with a class containing two methods."""
    return b"class User:\n    def find(self):\n        pass\n\n    def all(self):\n        pass\n"


@pytest.fixture
def sample_user_model_trimmed_source() -> bytes:
    """Same class with one method removed."""
    return b"class User:\n    def find(self):\n        pass\n"


@pytest.fixture
def sample_user_service_source() -> bytes:
    """Python source with a top-level function."""
    return b"from app.models.user import User\n\ndef get_user(user_id):\n    return User.find(user_id)\n"