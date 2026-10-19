"""Analyses de sécurité : bandit (code) + pip-audit (dépendances).

Usage : python scripts/security_check.py
truststore : utilise le magasin de certificats du système (utile derrière un
antivirus/proxy qui inspecte le trafic HTTPS) sans désactiver la vérification SSL.
"""
import subprocess
import sys

import truststore

truststore.inject_into_ssl()

from pip_audit._cli import audit  # noqa: E402


def main() -> int:
    print("== bandit ==")
    code = subprocess.call([sys.executable, "-m", "bandit", "-q", "-r", "app"])
    print("== pip-audit ==")
    sys.argv = ["pip-audit"]
    try:
        audit()
    except SystemExit as e:
        code = code or (e.code or 0)
    return code


if __name__ == "__main__":
    sys.exit(main())
