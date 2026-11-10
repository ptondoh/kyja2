"""Filtres des listes : chaque filtre est une zone de saisie (avec liste déroulante) au-dessus de sa colonne."""
import unicodedata


def plain(text: str) -> str:
    """Minuscules sans accents : « Passés » et « passes » sont la même chose."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c)).strip()


def matching(text: str, choices: dict[str, str]) -> list[str] | None:
    """Clés dont le libellé contient le texte tapé (ou dont la clé est exactement ce texte). None si la zone est vide."""
    if not text or not text.strip():
        return None
    t = plain(text)
    return [key for key, label in choices.items() if t == plain(key) or t in plain(label)]
