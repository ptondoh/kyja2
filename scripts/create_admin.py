"""Crée un compte administrateur.

Usage : python -m scripts.create_admin email@exemple.com "Nom Complet"
Le mot de passe est demandé en console (jamais passé en argument ni journalisé).
"""
import getpass
import sys

from sqlalchemy import select

from app.auth.security import hash_password
from app.db.base import SessionLocal
from app.db.models.core import Role, User

MIN_PASSWORD = 12


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    email, name = sys.argv[1].strip().lower(), sys.argv[2].strip()
    password = getpass.getpass("Mot de passe (12 caractères minimum) : ")
    if len(password) < MIN_PASSWORD:
        print(f"Mot de passe trop court (minimum {MIN_PASSWORD}).")
        return 1
    if getpass.getpass("Confirmer : ") != password:
        print("Les mots de passe ne correspondent pas.")
        return 1
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == email)):
            print("Un compte existe déjà avec cet email.")
            return 1
        db.add(User(email=email, password_hash=hash_password(password), role=Role.admin, full_name=name))
        db.commit()
    print(f"Administrateur créé : {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
