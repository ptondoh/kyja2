import uuid
from dataclasses import dataclass
from pathlib import Path

import magic
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models.core import Media, MediaStatus, MediaType
from app.db.models.settings import get_int_setting
from app.media import probe
from app.media.storage import get_storage

# Liste blanche : le type est détecté sur le contenu réel, jamais sur l'extension.
IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/webm"}

IMAGE_MAX_SIDE = 1600
VIDEO_MAX_HEIGHT = 720
Image.MAX_IMAGE_PIXELS = 40_000_000  # anti decompression bomb
CHUNK = 1024 * 1024


class UploadRejected(ValueError):
    """Fichier refusé ; le message est affichable à l'admin."""


@dataclass
class StagedUpload:
    path: Path
    kind: MediaType
    mime: str
    size: int
    duration_s: int | None = None


def _tmp_dir() -> Path:
    d = Path(get_settings().upload_tmp_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def stage_upload(db: Session, file: UploadFile) -> StagedUpload:
    """Copie le fichier vers un temporaire en plafonnant la taille, puis le valide.

    Lève UploadRejected (et supprime le temporaire) si le fichier est refusé.
    """
    s = get_settings()
    max_video = get_int_setting(db, "video_max_mb") * 1024 * 1024
    max_image = s.image_max_mb * 1024 * 1024
    hard_cap = max(max_video, max_image)

    tmp = _tmp_dir() / f"{uuid.uuid4().hex}.upload"
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := file.file.read(CHUNK):
                size += len(chunk)
                if size > hard_cap:
                    raise UploadRejected(f"Fichier trop volumineux (max {hard_cap // 2**20} Mo).")
                out.write(chunk)
        if size == 0:
            raise UploadRejected("Fichier vide.")

        with tmp.open("rb") as f:
            mime = magic.from_buffer(f.read(4096), mime=True)

        if mime in IMAGE_MIMES:
            if size > max_image:
                raise UploadRejected(f"Image trop volumineuse (max {s.image_max_mb} Mo).")
            try:
                with Image.open(tmp) as im:
                    im.verify()
            except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
                raise UploadRejected("Image illisible ou corrompue.") from None
            return StagedUpload(tmp, MediaType.image, mime, size)

        if mime in VIDEO_MIMES:
            if size > max_video:
                raise UploadRejected(
                    f"Vidéo trop volumineuse (max {max_video // 2**20} Mo)."
                )
            try:
                info = probe.probe_video(tmp)
            except probe.FfmpegUnavailable:
                raise UploadRejected("Le traitement vidéo est indisponible (ffmpeg manquant).") from None
            if info is None:
                raise UploadRejected("Vidéo illisible ou corrompue.")
            max_s = get_int_setting(db, "video_max_seconds")
            if info.duration_s > max_s:
                raise UploadRejected(f"Vidéo trop longue (max {max_s // 60} min {max_s % 60:02d} s).")
            return StagedUpload(tmp, MediaType.video, mime, size, round(info.duration_s))

        raise UploadRejected("Type de fichier non autorisé (JPEG, PNG, WebP, MP4, MOV ou WebM).")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def create_media(db: Session, staged: StagedUpload, *, service_id=None, product_id=None) -> Media:
    last = db.scalar(
        select(func.max(Media.position)).where(
            Media.service_id == service_id if service_id else Media.product_id == product_id
        )
    )
    media = Media(
        service_id=service_id,
        product_id=product_id,
        type=staged.kind,
        mime_type=staged.mime,
        size_bytes=staged.size,
        duration_s=staged.duration_s,
        status=MediaStatus.processing,
        position=(last if last is not None else -1) + 1,
    )
    db.add(media)
    db.commit()
    return media


def _owner_prefix(media: Media) -> str:
    return f"services/{media.service_id}" if media.service_id else f"products/{media.product_id}"


def process_media(media_id: int, tmp_path: str) -> None:
    """Optimise le média et le publie. Appelée en tâche de fond (Celery plus tard)."""
    path = Path(tmp_path)
    work = _tmp_dir() / f"work-{uuid.uuid4().hex}"
    work.mkdir()
    storage = get_storage()
    with SessionLocal() as db:
        media = db.get(Media, media_id)
        if media is None:
            path.unlink(missing_ok=True)
            return
        try:
            prefix, token = _owner_prefix(media), uuid.uuid4().hex
            if media.type == MediaType.image:
                _process_image(media, path, work, storage, prefix, token)
            else:
                _process_video(media, path, work, storage, prefix, token)
            media.status = MediaStatus.ready
            media.error = None
        except Exception as exc:  # noqa: BLE001 - tout échec marque le média en erreur
            media.status = MediaStatus.failed
            media.error = f"{type(exc).__name__}"[:500]
        finally:
            db.commit()
            path.unlink(missing_ok=True)
            for f in work.iterdir():
                f.unlink(missing_ok=True)
            work.rmdir()


def _process_image(media: Media, src: Path, work: Path, storage, prefix: str, token: str) -> None:
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)  # applique l'orientation, puis retire les EXIF à l'enregistrement
        im = im.convert("RGB")
        im.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
        out = work / "image.webp"
        im.save(out, "WEBP", quality=82)
        media.width, media.height = im.size
    media.storage_key = f"{prefix}/{token}.webp"
    media.url = storage.save(out, media.storage_key, "image/webp")
    media.mime_type = "image/webp"
    media.size_bytes = out.stat().st_size


def _process_video(media: Media, src: Path, work: Path, storage, prefix: str, token: str) -> None:
    out = work / "video.mp4"
    probe.run_ffmpeg(
        [
            "-i", str(src),
            "-vf", f"scale=-2:'min({VIDEO_MAX_HEIGHT},ih)'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(out),
        ]
    )  # fmt: skip
    poster = work / "poster.webp"
    at = "1" if (media.duration_s or 0) > 2 else "0"
    jpg = work / "poster.jpg"
    probe.run_ffmpeg(["-ss", at, "-i", str(out), "-frames:v", "1", str(jpg)])
    with Image.open(jpg) as im:
        im.convert("RGB").save(poster, "WEBP", quality=80)
    info = probe.probe_video(out)
    if info:
        media.width, media.height, media.duration_s = info.width, info.height, round(info.duration_s)
    media.storage_key = f"{prefix}/{token}.mp4"
    media.poster_key = f"{prefix}/{token}-poster.webp"
    media.url = storage.save(out, media.storage_key, "video/mp4")
    media.poster_url = storage.save(poster, media.poster_key, "image/webp")
    media.mime_type = "video/mp4"
    media.size_bytes = out.stat().st_size


def delete_media_files(media: Media) -> None:
    storage = get_storage()
    for key in (media.storage_key, media.poster_key):
        if key:
            storage.delete(key)

