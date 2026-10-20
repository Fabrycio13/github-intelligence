"""One-time verified Windows connection setup; no remote writes."""

import argparse
import getpass
import json
import os
from pathlib import Path
from uuid import uuid4

from github_intelligence.config import ConfigurationError
from github_intelligence.database import _POOLER, _WORKER, DatabaseSettings, connect_database
from github_intelligence.db_health import check_database
from github_intelligence.secrets import SecretError, protect_private_key
from github_intelligence.storage import StorageError


def save_verified_connection(settings: DatabaseSettings, config: Path, secret: Path) -> None:
    """Verify first, then save a new DPAPI credential and nonsecret configuration."""
    if config.exists() or secret.exists():
        raise ConfigurationError("Configuração já existe; não será sobrescrita.")
    with connect_database(settings) as connection:
        check_database(connection)
    config.parent.mkdir(parents=True, exist_ok=True)
    secret.parent.mkdir(parents=True, exist_ok=True)
    protect_private_key(settings.password.encode("utf-8"), secret)
    try:
        with config.open("x", encoding="utf-8") as stream:
            json.dump({
                "host": settings.host,
                "user": settings.user,
                "project_ref": settings.project_ref,
                "sslrootcert": settings.sslrootcert,
                "password_path": str(secret.resolve()),
            }, stream, indent=2)
    except OSError:
        secret.unlink(missing_ok=True)
        raise ConfigurationError("Não foi possível salvar a configuração local.") from None


def main() -> int:
    parser = argparse.ArgumentParser(description="Configurar conexão uma vez, sem gravação remota")
    parser.add_argument("--host", required=True)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--sslrootcert", required=True)
    args = parser.parse_args()
    config = Path("config/database.local.json")
    if config.exists():
        print("Configuração já existe; use uv run github-intelligence db check.")
        return 2
    try:
        password = os.environ.pop("GHI_SETUP_PASSWORD", None)
        if password is None:
            password = getpass.getpass("Senha do worker: ")
        user = f"{_WORKER}.{args.project_ref}" if _POOLER.fullmatch(args.host) else _WORKER
        settings = DatabaseSettings(
            host=args.host, user=user, project_ref=args.project_ref,
            password=password, sslrootcert=args.sslrootcert,
        )
        secret = Path.home() / ".github-intelligence" / f"worker-{uuid4().hex}.dpapi"
        save_verified_connection(settings, config, secret)
        # Reload the persisted configuration, not just the in-memory input.
        with connect_database(DatabaseSettings.from_environment({}, config_path=config)) as connection:
            check_database(connection)
    except (ConfigurationError, StorageError, SecretError, OSError) as error:
        if isinstance(error, OSError):
            print("Falha ao salvar configuração local.")
        else:
            print(f"Erro: {error}")
        return 2
    print("PASS: conexão verificada e credencial criptografada salva. Não precisa digitar novamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
