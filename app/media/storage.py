import mimetypes
import shutil
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class Storage(Protocol):
    def save(self, local_path: Path, key: str, content_type: str) -> str:
        """Enregistre le fichier sous `key` et retourne son URL publique."""

    def delete(self, key: str) -> None: ...


class LocalStorage:
    """Stockage sur disque (dev). Servi sous /media-files par l'application."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:  # anti path traversal
            raise ValueError("clé de stockage invalide")
        return p

    def save(self, local_path: Path, key: str, content_type: str) -> str:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, dest)
        return f"/media-files/{key}"

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class R2Storage:
    """Cloudflare R2 (compatible S3), servi via le CDN."""

    def __init__(self):
        import boto3

        s = get_settings()
        self.bucket = s.r2_bucket
        self.public_base = s.r2_public_base_url.rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=s.r2_endpoint,
            aws_access_key_id=s.r2_access_key,
            aws_secret_access_key=s.r2_secret_key,
            region_name="auto",
        )

    def save(self, local_path: Path, key: str, content_type: str) -> str:
        content_type = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        self.client.upload_file(
            str(local_path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type, "CacheControl": "public, max-age=31536000"},
        )
        return f"{self.public_base}/{key}"

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def get_storage() -> Storage:
    s = get_settings()
    if s.r2_endpoint and s.r2_public_base_url:
        return R2Storage()
    return LocalStorage(Path(s.media_dir))
