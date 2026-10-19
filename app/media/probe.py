import re
import shutil
# ffmpeg est appelé avec une liste d arguments, sans shell.
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_SIZE = re.compile(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})")

PROBE_TIMEOUT_S = 30
TRANSCODE_TIMEOUT_S = 600


class FfmpegUnavailable(RuntimeError):
    pass


@dataclass
class VideoInfo:
    duration_s: float
    width: int | None
    height: int | None


def find_ffmpeg() -> str:
    s = get_settings()
    if s.ffmpeg_bin:
        return s.ffmpeg_bin
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # binaire embarqué (dev) si ffmpeg n'est pas installé sur le système
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise FfmpegUnavailable("ffmpeg est introuvable") from exc


def probe_video(path: Path) -> VideoInfo | None:
    """Lit durée et dimensions via `ffmpeg -i`. Retourne None si ce n'est pas une vidéo lisible."""
    try:
        proc = subprocess.run(  # nosec B603
            [find_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None
    out = proc.stderr
    m = _DURATION.search(out)
    if not m or "Video:" not in out:
        return None
    h, mn, sec = m.groups()
    size = _SIZE.search(out)
    return VideoInfo(
        duration_s=int(h) * 3600 + int(mn) * 60 + float(sec),
        width=int(size.group(1)) if size else None,
        height=int(size.group(2)) if size else None,
    )


def run_ffmpeg(args: list[str]) -> None:
    subprocess.run(  # nosec B603
        [find_ffmpeg(), "-hide_banner", "-nostdin", "-y", *args],
        capture_output=True,
        timeout=TRANSCODE_TIMEOUT_S,
        check=True,
    )
