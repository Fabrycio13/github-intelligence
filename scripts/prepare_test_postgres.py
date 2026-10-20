"""Prepare native PostgreSQL 17.11 binaries, without an installer or service.

Source: PostgreSQL's Windows download links to EnterpriseDB; EDB's binaries page
links fileid=1260616 to the pinned ZIP below. SHA256 was measured from that
HTTPS download (not an independently published vendor signature).
Run: uv run python scripts/prepare_test_postgres.py
An alternate trusted HTTPS ZIP requires --url AND --sha256. No scripts are run.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

VERSION = "17.11"
SOURCE_URL = "https://get.enterprisedb.com/postgresql/postgresql-17.11-5-windows-x64-binaries.zip"
SHA256 = "80379b2c04d51c30225532e0ae04509899141e9957ed096fe749d7fd9df8f82f"
HERMES_HOME = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "hermes"
CACHE = HERMES_HOME / "tools/postgresql-17.11-5"
SCRATCH = HERMES_HOME / "cache/scratch"


def validate_bin(binary_dir: Path) -> str:
    for name in ("postgres.exe", "initdb.exe", "pg_ctl.exe"):
        if not (binary_dir / name).is_file():
            raise ValueError(f"Missing native PostgreSQL binary: {binary_dir / name}")
    result = subprocess.run(
        [str(binary_dir / "postgres.exe"), "--version"],
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout.strip()
    if not re.fullmatch(r"postgres \(PostgreSQL\) 17\.11(?: .*)?", result):
        raise ValueError(f"Expected PostgreSQL {VERSION}; got {result!r}")
    return result


def safe_extract(archive: Path, destination: Path) -> None:
    """Reject traversal, Windows drive/ADS names and symlinks before extraction."""
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if sum(item.file_size for item in members) > 2_000_000_000:
            raise ValueError("ZIP expands beyond 2 GB")
        for item in members:
            name = item.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if (
                not parts or name.startswith("/") or ":" in name
                or any(p in (".", "..") or p.endswith((" ", ".")) for p in parts)
                or stat.S_ISLNK(item.external_attr >> 16)
                or not (root / name).resolve().is_relative_to(root)
            ):
                raise ValueError(f"Unsafe ZIP member: {item.filename!r}")
        # Only engine files; do not extract/install pgAdmin or StackBuilder.
        for item in members:
            name = item.filename.replace("\\", "/")
            if not name.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/")):
                continue
            target = root / name
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def prepare(url: str = SOURCE_URL, digest: str = SHA256, cache: Path = CACHE) -> Path:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Source must be HTTPS without credentials")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Expected a lowercase SHA256 hex digest")
    binary_dir = cache / "pgsql/bin"
    if cache.exists():
        manifest = json.loads((cache / "source.json").read_text(encoding="utf-8"))
        if manifest["url"] != url or manifest["sha256"] != digest:
            raise ValueError("Existing cache provenance differs; choose a different --cache")
        validate_bin(binary_dir)
        return binary_dir
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ghi-pg-prepare-", dir=SCRATCH) as temp:
        stage = Path(temp)
        archive = stage / "postgresql.zip"
        hasher = hashlib.sha256()
        with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            if response.url.scheme != "https":
                raise ValueError("Refusing non-HTTPS redirect")
            with archive.open("wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    hasher.update(chunk)
                    output.write(chunk)
                    if output.tell() > 1_000_000_000:
                        raise ValueError("Download exceeds 1 GB")
            resolved_url = str(response.url)
        if hasher.hexdigest() != digest:
            raise ValueError("SHA256 mismatch; nothing extracted or executed")
        unpacked = stage / "unpacked"
        safe_extract(archive, unpacked)
        actual_version = validate_bin(unpacked / "pgsql/bin")
        (unpacked / "source.json").write_text(
            json.dumps({"url": url, "resolved_url": resolved_url, "sha256": digest,
                        "version": actual_version}, indent=2) + "\n", encoding="utf-8",
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(unpacked), str(cache))
    return binary_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=SOURCE_URL)
    parser.add_argument("--sha256", default=SHA256)
    parser.add_argument("--cache", type=Path, default=CACHE)
    args = parser.parse_args()
    if args.url != SOURCE_URL and args.sha256 == SHA256:
        parser.error("An alternate --url requires its explicit --sha256")
    binary_dir = prepare(args.url, args.sha256, args.cache.resolve())
    print(f"GHI_PG_BIN={binary_dir}")
    print(validate_bin(binary_dir))
    print(f"Source: {args.url}")


if __name__ == "__main__":
    main()
