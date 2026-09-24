"""Settings changed from the interface, one JSON document per section (table app_settings)."""
import json
import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from .models import AppSetting
from .schemas import (
    BackupSettings, ModelSettings, OnboardingSettings, QualitySettings, UrlImportSettings, WatchFolderSettings,
)

logger = logging.getLogger(__name__)

WATCH_FOLDER = "watch_folder"
BACKUPS = "backups"
URL_IMPORT = "url_import"
MODELS = "models"
QUALITY = "quality"
ONBOARDING = "onboarding"
# The password hash and the session key: read by app.auth only, never returned by the API.
ACCESS = "access"
SECTIONS: dict[str, type[BaseModel]] = {
    WATCH_FOLDER: WatchFolderSettings, BACKUPS: BackupSettings, URL_IMPORT: UrlImportSettings, MODELS: ModelSettings,
    QUALITY: QualitySettings, ONBOARDING: OnboardingSettings,
}

Model = TypeVar("Model", bound=BaseModel)


def load(db: Session, key: str, model: type[Model]) -> Model:
    """The stored section, or its defaults when missing or no longer valid."""
    row = db.get(AppSetting, key)
    if row is None:
        return model()
    try:
        return model.model_validate(json.loads(row.value))
    except (ValueError, ValidationError):
        logger.warning("Invalid stored settings %s: defaults used", key)
        return model()


def save(db: Session, key: str, value: BaseModel) -> None:
    """Stage the section in the session; the caller commits."""
    encoded = value.model_dump_json()
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=encoded))
    else:
        row.value = encoded
