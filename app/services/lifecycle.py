"""Suppression et archivage : catégories, prestations, produits et coiffeuses.

Règle générale : « supprimer » ne doit jamais abîmer l'historique.
  * jamais utilisé  -> supprimé pour de bon (fichiers compris) ;
  * déjà des rendez-vous -> **archivé** : invisible partout (site, réservation, listes), mais les rendez-vous
    passés gardent leur prestation / leur coiffeuse, et les coordonnées restent en base ;
  * des rendez-vous **à venir** -> refusé (il faut d'abord les annuler), pour ne pas laisser de cliente avec un
    rendez-vous sur une prestation ou une coiffeuse qui n'existe plus.
"""
import json
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.auth.service import revoke_all_tokens
from app.db.models.core import (
    ACTIVE_STATUSES,
    Appointment,
    AppointmentStatus,
    Category,
    Hairdresser,
    Media,
    Product,
    Service,
)
from app.media.service import delete_media_files
from app.media.storage import get_storage


class InUse(Exception):
    """L'élément a des rendez-vous à venir : il faut d'abord les annuler."""

    def __init__(self, blockers: list[tuple[str, int]]):
        self.blockers = blockers  # [(nom, nombre de rendez-vous à venir)]
        super().__init__(", ".join(f"{name} ({n})" for name, n in blockers))


class Protected(Exception):
    """Élément indispensable au fonctionnement (la prestation intégrée « Demande sur mesure »)."""


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def future_count(db: Session, *, service_id: int | None = None, hairdresser_id: int | None = None, now: datetime | None = None) -> int:
    q = select(func.count()).select_from(Appointment).where(
        Appointment.status.in_(ACTIVE_STATUSES), Appointment.starts_at > _now(now)
    )
    if service_id is not None:
        q = q.where(Appointment.service_id == service_id)
    if hairdresser_id is not None:
        q = q.where(Appointment.hairdresser_id == hairdresser_id)
    return db.scalar(q) or 0


def has_history(db: Session, *, service_id: int | None = None, hairdresser_id: int | None = None) -> bool:
    q = select(Appointment.id).limit(1)
    if service_id is not None:
        q = q.where(Appointment.service_id == service_id)
    if hairdresser_id is not None:
        q = q.where(Appointment.hairdresser_id == hairdresser_id)
    return db.scalar(q) is not None


# --- prestations ----------------------------------------------------------------------------------------------------


@dataclass
class Removal:
    deleted: list[str] = field(default_factory=list)  # supprimées pour de bon
    archived: list[str] = field(default_factory=list)  # archivées (historique conservé)


def _purge_media_files(medias: list[Media]) -> None:
    for m in medias:
        delete_media_files(m)


def _remove_service(db: Session, service: Service, now: datetime, out: Removal) -> list[Media]:
    """Supprime ou archive (sans valider la transaction). Retourne les médias dont les fichiers sont à effacer."""
    if service.is_custom_request:
        raise Protected(service.name)
    if has_history(db, service_id=service.id):
        service.archived_at, service.is_active, service.category_id = now, False, None
        out.archived.append(service.name)
        return []
    medias = list(service.media)
    out.deleted.append(service.name)
    db.delete(service)
    return medias


def remove_service(db: Session, service: Service, now: datetime | None = None) -> Removal:
    now = _now(now)
    n = future_count(db, service_id=service.id, now=now)
    if n:
        raise InUse([(service.name, n)])
    out = Removal()
    medias = _remove_service(db, service, now, out)
    db.commit()
    _purge_media_files(medias)  # les fichiers ne sont effacés qu'après la validation en base
    return out


def restore_service(db: Session, service: Service) -> None:
    service.archived_at, service.is_active = None, True
    db.commit()


def remove_product(db: Session, product: Product) -> None:
    """Pas encore de commandes : suppression définitive (fichiers compris). Avec les commandes (phase 2), il
    faudra archiver comme pour les prestations."""
    medias = list(product.media)
    db.delete(product)
    db.commit()
    _purge_media_files(medias)


# --- catégories -----------------------------------------------------------------------------------------------------


def category_services(db: Session, category: Category) -> list[Service]:
    return list(
        db.scalars(
            select(Service).options(selectinload(Service.media)).where(Service.category_id == category.id).order_by(Service.name)
        )
    )


def category_overview(db: Session, category: Category, now: datetime | None = None) -> dict:
    """Ce que la suppression de la catégorie ferait, pour l'afficher avant que l'admin confirme."""
    rows = []
    for s in category_services(db, category):
        rows.append(
            {
                "service": s,
                "history": has_history(db, service_id=s.id),
                "future": future_count(db, service_id=s.id, now=now),
                "media": len(s.media),
            }
        )
    return {
        "rows": rows,
        "to_delete": [r for r in rows if not r["history"]],
        "to_archive": [r for r in rows if r["history"]],
        "blockers": [(r["service"].name, r["future"]) for r in rows if r["future"]],
        "media_total": sum(r["media"] for r in rows if not r["history"]),
    }


def remove_category(db: Session, category: Category, now: datetime | None = None) -> Removal:
    """Supprime la catégorie ET ses prestations : effacées si jamais réservées, archivées sinon."""
    now = _now(now)
    overview = category_overview(db, category, now)
    if overview["blockers"]:
        raise InUse(overview["blockers"])
    out = Removal()
    medias: list[Media] = []
    for row in overview["rows"]:
        medias += _remove_service(db, row["service"], now, out)
    db.flush()
    db.delete(category)  # les prestations archivées ont déjà perdu leur catégorie (SET NULL)
    db.commit()
    _purge_media_files(medias)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().encode("ascii", "ignore").decode() or "categorie").strip("-") or "categorie"


def backup_filename(category: Category, now: datetime | None = None) -> str:
    return f"kyja-sauvegarde-{_slug(category.name)}-{_now(now).strftime('%Y%m%d')}.zip"


README_BACKUP = """Sauvegarde d'une catégorie de KYJA BEAUTÉ
==========================================
categorie.json : la catégorie et toutes ses prestations (nom, description, durée, prix, équipements, médias).
medias/        : les photos et vidéos de chaque prestation.

Cette sauvegarde est une COPIE DE SÉCURITÉ lisible : elle permet de retrouver les textes, les prix et les
fichiers si la catégorie a été supprimée par erreur. Elle ne contient aucune donnée de cliente.
"""


def backup_category_zip(db: Session, category: Category, dest: Path, now: datetime | None = None) -> None:
    """Écrit un ZIP : `categorie.json` + les fichiers médias de toutes les prestations de la catégorie."""
    storage = get_storage()
    services = category_services(db, category)
    exported, media_files = [], []
    for s in services:
        medias = []
        for m in s.media:
            entry = {"type": m.type.value, "status": m.status.value, "mime": m.mime_type, "width": m.width,
                     "height": m.height, "duration_s": m.duration_s, "position": m.position}  # fmt: skip
            for key_field, label in (("storage_key", "file"), ("poster_key", "poster")):
                key = getattr(m, key_field)
                if key:
                    arc = f"medias/{s.id}/{m.id}-{label}{Path(key).suffix}"
                    entry[label] = arc
                    media_files.append((key, arc))
            medias.append(entry)
        exported.append(
            {
                "id": s.id, "name": s.name, "description": s.description,
                "base_duration_min": s.base_duration_min, "buffer_min": s.buffer_min,
                "pricing_mode": s.pricing_mode.value, "price": str(s.price) if s.price is not None else None,
                "is_active": s.is_active, "required_equipment": sorted(r.resource_kind for r in s.resource_types),
                "media": medias,
            }
        )  # fmt: skip
    payload = {
        "format_version": 1,
        "exported_at": _now(now).isoformat(),
        "category": {"id": category.id, "name": category.name},
        "services": exported,
    }
    with zipfile.ZipFile(dest, "w") as z:
        z.writestr("LISEZ-MOI.txt", README_BACKUP, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("categorie.json", json.dumps(payload, ensure_ascii=False, indent=2), compress_type=zipfile.ZIP_DEFLATED)
        with tempfile.TemporaryDirectory() as tmp:
            for key, arc in media_files:
                local = Path(tmp) / "f"
                try:
                    storage.fetch(key, local)
                except (FileNotFoundError, OSError):
                    z.writestr(arc + ".MANQUANT", "Fichier introuvable dans le stockage au moment de la sauvegarde.")
                    continue
                z.write(local, arc, compress_type=zipfile.ZIP_STORED)  # déjà compressés (webp, mp4)
                local.unlink(missing_ok=True)


# --- coiffeuses -----------------------------------------------------------------------------------------------------


def archive_hairdresser(db: Session, h: Hairdresser, cancel_future: bool = False, now: datetime | None = None) -> int:
    """« Supprime » la coiffeuse : elle disparaît du site, de la réservation et des listes, mais son historique
    et ses coordonnées restent. Retourne le nombre de rendez-vous à venir annulés."""
    now = _now(now)
    n = future_count(db, hairdresser_id=h.id, now=now)
    if n and not cancel_future:
        raise InUse([(h.user.full_name, n)])
    cancelled = 0
    if n:
        cancelled = db.execute(
            update(Appointment)
            .where(Appointment.hairdresser_id == h.id, Appointment.status.in_(ACTIVE_STATUSES), Appointment.starts_at > now)
            .values(status=AppointmentStatus.cancelled, cancel_reason="hairdresser_removed", quote_token_hash=None)
        ).rowcount
    h.archived_at, h.is_active = now, False
    h.user.is_active = False  # plus de connexion possible ; nom, email et téléphone restent
    db.flush()
    revoke_all_tokens(db, h.user_id)  # valide la transaction ; coupe les sessions ouvertes
    return cancelled


def restore_hairdresser(db: Session, h: Hairdresser) -> None:
    h.archived_at, h.is_active = None, True
    if h.user.anonymized_at is None:
        h.user.is_active = True
    db.commit()
