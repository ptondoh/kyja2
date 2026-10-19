from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    secret_key: str = "change-me"
    database_url: str = "postgresql+psycopg://kyja:kyja@localhost:5432/kyja"
    redis_url: str = "redis://localhost:6379/0"

    r2_endpoint: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = "kyja-beaute"
    r2_public_base_url: str = ""

    resend_api_key: str = ""
    email_from: str = "KYJA BEAUTÉ <no-reply@example.com>"

    upload_tmp_dir: str = "./tmp_uploads"
    media_dir: str = "./media_files"  # stockage local si R2 n'est pas configuré
    image_max_mb: int = 10
    ffmpeg_bin: str = ""

    access_token_minutes: int = 15
    refresh_token_days: int = 30
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
