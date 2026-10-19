"""Détache le fond vert du logo et produit les variantes utilisées par le site.

Usage : python scripts/make_logo.py
Entrée  : design/logo-source.png (or sur fond vert citron)
Sorties : app/static/img/logo.png (détouré), logo-mark.png, logo-512.webp, favicon.png
"""
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "design" / "logo-source.png"
OUT = ROOT / "app" / "static" / "img"

LOW, HIGH = 20, 60  # (g - r) : <= LOW opaque, >= HIGH transparent


def fix_accent(img: Image.Image) -> Image.Image:
    """Le logo d'origine écrit « BEAUTÈ » (accent grave) : on retourne l'accent pour obtenir « BEAUTÉ »."""
    box = (434, 556, 448, 565)  # accent seul, au-dessus du dernier E
    img = img.copy()
    img.paste(img.crop(box).transpose(Image.FLIP_LEFT_RIGHT), box)
    return img


def main() -> None:
    im = np.asarray(fix_accent(Image.open(SRC).convert("RGB"))).astype(float)
    r, g, b = im[..., 0], im[..., 1], im[..., 2]
    bg = np.median(im[:12, :12].reshape(-1, 3), axis=0)  # couleur du fond

    alpha = np.clip((HIGH - (g - r)) / (HIGH - LOW), 0, 1)
    # Retire la contribution du fond sur les pixels de bord (évite le halo vert).
    a = np.maximum(alpha, 1e-3)[..., None]
    fg = np.clip((im - (1 - a) * bg) / a, 0, 255)
    fg[alpha < 0.02] = 0

    rgba = np.dstack([fg, alpha * 255]).astype(np.uint8)
    out = Image.fromarray(rgba, "RGBA")
    out = out.crop(out.getbbox())
    OUT.mkdir(parents=True, exist_ok=True)
    out.save(OUT / "logo.png", optimize=True)
    out.resize((512, round(512 * out.height / out.width)), Image.LANCZOS).save(
        OUT / "logo-512.webp", quality=90
    )

    # Monogramme KB (cercle supérieur) : en-tête du site et icône du navigateur.
    w, h = out.size
    mono = out.crop((0, 0, w, int(h * 0.70)))
    mono.resize((160, round(160 * mono.height / mono.width)), Image.LANCZOS).save(
        OUT / "logo-mark.png", optimize=True
    )
    side = max(mono.size)
    canvas = Image.new("RGBA", (side, side), (26, 26, 26, 255))
    canvas.alpha_composite(mono, ((side - mono.width) // 2, (side - mono.height) // 2))
    canvas.resize((192, 192), Image.LANCZOS).save(OUT / "favicon.png", optimize=True)
    print("logo", out.size, "bg", bg)


if __name__ == "__main__":
    main()
