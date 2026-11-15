"""Print a PBKDF2 hash for ADMIN_PASSWORD_HASH (so the plain password never sits in .env).

  .venv\\Scripts\\python scripts\\hash_password.py
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portfolio.security import hash_password  # noqa: E402

if __name__ == "__main__":
    first = getpass.getpass("New admin password: ")
    if len(first) < 8:
        sys.exit("Use at least 8 characters.")
    if getpass.getpass("Repeat: ") != first:
        sys.exit("Passwords do not match.")
    print("\nPut this in .env (and remove ADMIN_PASSWORD):\n")
    print(f"ADMIN_PASSWORD_HASH={hash_password(first)}")
