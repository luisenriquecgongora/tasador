"""Publish a Tasador release to Cloudflare R2 (the GitHub Release gets the same files).

Takes the installers that build_installer.py made on each system (all in dist/), writes
dist/version.json (version, file, size, SHA-256 and URLs per platform), and uploads them to
two places in the bucket:
  tasador/v<version>/  immutable, cached for a year
  tasador/latest/      short cache, always the newest release (the website links here)
then enforces the retention cap. A failed prune fails the publish, so the cap can never
be silently skipped. Every platform must be present, so a release is never half published.

Usage:
    set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY (and optionally R2_BUCKET_NAME)
    python scripts/publish_release_to_r2.py --version 0.1.0
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prune_r2_releases import MAX_RELEASES, get_r2_client, prune_prefix_releases, validate_max_keep  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("r2.publisher")

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"
PRODUCT = "Tasador"
DEFAULT_PUBLIC_DOMAIN = "https://pub-fb9de4da4f48439ebb34b3795d05ab11.r2.dev"
KEEP = 3  # releases kept in the bucket (inside the R2 free tier); GitHub keeps them all
REPO = "https://github.com/luisenriquecgongora/tasador"
# platform -> (file suffix, version.json key, content type)
PLATFORMS = {
    "windows-x64": ("-windows-x64-setup.exe", "windows_x64", "application/octet-stream"),
    "macos-arm64": ("-macos-arm64.dmg", "macos_arm64", "application/x-apple-diskimage"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_installers(dist: Path, version: str) -> dict:
    """{platform: installer path} for every platform, or exits naming what is missing."""
    found = {platform: dist / f"{PRODUCT}-v{version}{suffix}" for platform, (suffix, _, _) in PLATFORMS.items()}
    missing = [path.name for path in found.values() if not path.exists()]
    if missing:
        raise SystemExit(f"Missing {', '.join(missing)} in {dist}. Build each with scripts/build_installer.py --version {version}.")
    return found


def build_manifest(version: str, installers: dict, public: str, prefix: str) -> dict:
    manifest = {
        "product": PRODUCT,
        "version": version,
        "released_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    for platform, path in installers.items():
        suffix, key, _ = PLATFORMS[platform]
        manifest[key] = {
            "file": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
            "url": f"{public}/{prefix}/v{version}/{path.name}",
            "latest_url": f"{public}/{prefix}/latest/{PRODUCT}{suffix}",
        }
    manifest["release_page"] = f"{REPO}/releases/tag/v{version}"
    return manifest


def upload_with_retry(s3, src: Path, bucket: str, key: str, content_type: str, cache_control: str) -> None:
    for attempt in range(1, 4):
        try:
            s3.upload_file(str(src), bucket, key, ExtraArgs={"ContentType": content_type, "CacheControl": cache_control})
            logger.info("Uploaded %s -> %s (%.1f MB)", src.name, key, src.stat().st_size / 2**20)
            return
        except Exception as exc:
            if attempt == 3:
                raise
            logger.warning("Upload %d/3 of %s failed (%s); retrying in 2s...", attempt, key, exc)
            time.sleep(2)


def publish_release(version: str, prefix: str = "tasador", max_keep: int = KEEP) -> None:
    validate_max_keep(max_keep)
    bucket = os.environ.get("R2_BUCKET_NAME", "luisenrique-releases")
    public = os.environ.get("R2_CUSTOM_DOMAIN", DEFAULT_PUBLIC_DOMAIN).rstrip("/")

    installers = find_installers(DIST_DIR, version)
    manifest = DIST_DIR / "version.json"
    manifest.write_text(json.dumps(build_manifest(version, installers, public, prefix), indent=2), encoding="utf-8")

    s3 = get_r2_client()
    version_dir, latest_dir = f"{prefix}/v{version}", f"{prefix}/latest"
    immutable, short = "public, max-age=31536000, immutable", "public, max-age=300"
    uploads = []
    for platform, installer in installers.items():
        suffix, _, content_type = PLATFORMS[platform]
        uploads.append((installer, f"{version_dir}/{installer.name}", content_type, immutable))
        uploads.append((installer, f"{latest_dir}/{PRODUCT}{suffix}", content_type, short))
    # The manifests go last, so version.json never points at a file that is not there yet
    uploads.append((manifest, f"{version_dir}/version.json", "application/json", "public, max-age=86400"))
    uploads.append((manifest, f"{latest_dir}/version.json", "application/json", "no-cache, must-revalidate"))
    logger.info("Uploading %d objects to s3://%s/%s ...", len(uploads), bucket, prefix)
    for src, key, content_type, cache_control in uploads:
        upload_with_retry(s3, src, bucket, key, content_type, cache_control)

    for platform, (suffix, _, _) in PLATFORMS.items():
        logger.info("Latest %-12s %s/%s/%s%s", platform, public, latest_dir, PRODUCT, suffix)
    logger.info("Update manifest:    %s/%s/version.json", public, latest_dir)

    logger.info("Enforcing retention cap (max %d releases)...", max_keep)
    prune_prefix_releases(s3, bucket, prefix, max_keep=max_keep, protect_version=version)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish a Tasador release to Cloudflare R2")
    parser.add_argument("--version", required=True, help="Release version (e.g. 0.1.0)")
    parser.add_argument("--prefix", default="tasador", help="Bucket folder for Tasador releases")
    parser.add_argument("--max-keep", type=int, default=KEEP, help=f"Releases to keep, 1-{MAX_RELEASES}")
    args = parser.parse_args()
    publish_release(version=args.version.lstrip("v"), prefix=args.prefix, max_keep=args.max_keep)
