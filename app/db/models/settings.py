from sqlalchemy import String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base

# Valeurs par défaut des réglages modifiables par l'admin (cf. PRD.md, exigences vidéo).
DEFAULT_SETTINGS: dict[str, str] = {
    "video_max_mb": "100",
    "video_max_seconds": "180",
    "calendar_step_min": "15",  # pas du calendrier et arrondi des durées
    "booking_horizon_days": "60",  # jusqu'à quand on peut réserver
    "booking_min_notice_min": "60",  # délai minimal avant le début d'un rendez-vous
}


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))


def get_setting(db: Session, key: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row else DEFAULT_SETTINGS[key]


def get_int_setting(db: Session, key: str) -> int:
    return int(get_setting(db, key))


def set_setting(db: Session, key: str, value: str) -> None:
    if key not in DEFAULT_SETTINGS:
        raise KeyError(key)
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()
