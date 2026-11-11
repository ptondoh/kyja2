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
    exact = [key for key, label in choices.items() if t == plain(key) or t == plain(label)]
    return exact or [key for key, label in choices.items() if t in plain(label)]


def status_choices(feminine: bool = False) -> dict[str, str]:
    """Libellés du filtre « Statut » : actif / désactivé (au féminin pour une prestation, une catégorie, une coiffeuse)."""
    return {"actif": "Active" if feminine else "Actif", "inactif": "Désactivée" if feminine else "Désactivé"}


def apply_status(query, column, text: str, feminine: bool = False):
    """Ajoute le filtre « actif / désactivé » à une requête. Zone vide ou ambiguë (« act ») : aucun filtre."""
    from sqlalchemy import false

    found = matching(text, status_choices(feminine))
    if found is None or len(found) == 2:
        return query
    if not found:
        return query.where(false())
    return query.where(column.is_(True) if found == ["actif"] else column.is_(False))


def safe_next(raw: str | None, default: str) -> str:
    """Page de retour après une action : seulement une adresse de l'administration du site (jamais un site extérieur)."""
    if raw and raw.startswith("/admin") and "//" not in raw and "\\" not in raw and "\n" not in raw and "\r" not in raw:
        return raw
    return default
