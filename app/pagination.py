"""Pagination simple pour les listes : `?page=2` dans l'adresse, 20 lignes par page par défaut."""
from dataclasses import dataclass, field
from math import ceil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

PER_PAGE = 20


@dataclass
class Page:
    items: list[Any]
    page: int
    pages: int
    total: int
    per_page: int = PER_PAGE
    first: int = field(init=False)
    last: int = field(init=False)

    def __post_init__(self):
        self.first = (self.page - 1) * self.per_page + 1 if self.total else 0
        self.last = self.first + len(self.items) - 1 if self.total else 0

    @property
    def window(self) -> list[int | None]:
        """Numéros à afficher : 1 … autour de la page courante … dernière (None = « … »)."""
        wanted = sorted({1, self.pages, *range(max(1, self.page - 2), min(self.pages, self.page + 2) + 1)})
        out: list[int | None] = []
        for n in wanted:
            if out and n - (out[-1] or 0) > 1:
                out.append(None)
            out.append(n)
        return out


def paginate(db: Session, query, raw_page: str | int | None, per_page: int = PER_PAGE) -> Page:
    """Exécute `query` (un `select` déjà trié) pour la page demandée ; une page hors limites est ramenée dans les limites."""
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    pages = max(1, ceil(total / per_page))
    try:
        page = int(raw_page or 1)
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), pages)
    items = list(db.scalars(query.limit(per_page).offset((page - 1) * per_page)).all())
    return Page(items=items, page=page, pages=pages, total=total, per_page=per_page)


def like(text: str) -> str:
    """Motif `ILIKE '%…%'` : les % et _ saisis sont cherchés tels quels (à utiliser avec escape="\\\\")."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\%").replace("_", "\_") + "%"


def paginate_list(rows: list, raw_page: str | int | None, per_page: int = PER_PAGE) -> Page:
    """Même chose pour une liste déjà en mémoire (listes courtes d'une seule personne)."""
    pages = max(1, ceil(len(rows) / per_page))
    try:
        page = int(raw_page or 1)
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), pages)
    return Page(items=rows[(page - 1) * per_page : page * per_page], page=page, pages=pages, total=len(rows), per_page=per_page)
