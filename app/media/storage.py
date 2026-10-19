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


class PrivateStorage:
    """Stockage des fichiers personnels (photos des clientes) : aucune URL publique.

    Les fichiers ne sont lus que par l'application, après vérification des droits.
    Local en développement ; bucket R2 distinct (`r2_private_bucket`, sans accès public) en production.
    """

    def __init__(self):
        s = get_settings()
        self._r2 = None
        if s.r2_endpoint:
            import boto3

            self._bucket = s.r2_private_bucket
            self._r2 = boto3.client(
                "s3",
                endpoint_url=s.r2_endpoint,
                aws_access_key_id=s.r2_access_key,
                aws_secret_access_key=s.r2_secret_key,
                region_name="auto",
            )
        else:
            self._root = Path(s.private_dir).resolve()

    def _path(self, key: str) -> Path:
        p = (self._root / key).resolve()
        if self._root not in p.parents:  # anti path traversal
            raise ValueError("clé de stockage invalide")
        return p

    def save(self, data: bytes, key: str, content_type: str = "image/webp") -> None:
        if self._r2:
            self._r2.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
            return
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def read(self, key: str) -> bytes:
        if self._r2:
            return self._r2.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        if self._r2:
            self._r2.delete_object(Bucket=self._bucket, Key=key)
        else:
            self._path(key).unlink(missing_ok=True)


def get_private_storage() -> PrivateStorage:
    return PrivateStorage()
