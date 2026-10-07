import hashlib
import json
import re
import logging
from pathlib import Path

from log import LogFormatter

# Top-level constant for the release tag in download URLs
RELEASE_TAG = "dev-worlds-v1"
REPO_URL_BASE = f"https://github.com/BusinessDirt/Eurybium-Data/releases/download/{RELEASE_TAG}"


def to_kebab_case(name: str) -> str:
    """Converts a display name like 'Glacite Tunnels' to 'glacite-tunnels'."""
    # Replace non-alphanumeric characters with hyphens
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip())
    # Strip leading/trailing hyphens and lower-case
    return s.strip("-").lower()


def compute_sha256(file_path: Path) -> str:
    """Calculates the SHA-256 hex digest of a file in chunks."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            sha256.update(chunk)
    return sha256.hexdigest()


def main():
    handler = logging.StreamHandler()
    handler.setFormatter(LogFormatter(datefmt='%H:%M:%S'))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logger = logging.getLogger("dev_worlds")

    current_dir = Path.cwd()
    dev_dir = current_dir.parent / "dev"
    worlds_json_path = dev_dir / "worlds.json"
    worlds_dir_path = dev_dir / "worlds"
    hashes_file_path = worlds_dir_path / "hashes.txt"

    # Load existing worlds.json
    if not worlds_json_path.exists():
        logger.warn(f"{worlds_json_path} does not exist. Creating a fresh template.")
        data = {"schemaVersion": 1, "worlds": []}
    else:
        with open(worlds_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    # Index existing entries by their 'id' for fast lookups/updates
    worlds_by_id = {world["id"]: world for world in data.get("worlds", [])}

    # Rename .zip files and compute hashes
    processed_zips = []  # stores tuples of (new_filename, sha256_hash)

    # Sort files to ensure deterministic execution
    zip_files = sorted(worlds_dir_path.glob("*.zip"))

    for zip_path in zip_files:
        original_stem = zip_path.stem  # e.g. "Glacite Tunnels"
        kebab_id = to_kebab_case(original_stem)
        new_filename = f"{kebab_id}.zip"
        target_path = worlds_dir_path / new_filename

        # Rename if the filename doesn't already match
        if zip_path != target_path:
            zip_path.rename(target_path)
            logger.info(f"Renamed: '{zip_path.name}' -> '{new_filename}'")
        else:
            logger.info(f"File already normalized: '{new_filename}'")

        # Generate SHA-256 hash
        file_hash = compute_sha256(target_path)
        processed_zips.append((new_filename, file_hash))

        # Reconstruct human-readable name if not already tracked
        display_name = original_stem.replace("-", " ").title()
        download_url = f"{REPO_URL_BASE}/{new_filename}"

        # Update existing or add new entry in worlds.json structure
        if kebab_id in worlds_by_id:
            entry = worlds_by_id[kebab_id]
            entry["download"]["url"] = download_url
            entry["download"]["sha256"] = file_hash
        else:
            new_entry = {
                "id": kebab_id,
                "name": display_name,
                "download": {
                    "url": download_url,
                    "sha256": file_hash
                }
            }
            worlds_by_id[kebab_id] = new_entry

    # Write hashes.txt
    with open(hashes_file_path, "w", encoding="utf-8") as f:
        for filename, file_hash in processed_zips:
            f.write(f"- `{filename}`: `{file_hash}`\n")
    logger.info(f"Wrote {len(processed_zips)} entries to {hashes_file_path.name}")

    # Save updated worlds.json
    data["worlds"] = list(worlds_by_id.values())
    dev_dir.mkdir(parents=True, exist_ok=True)
    with open(worlds_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info(f"Updated {worlds_json_path}")


if __name__ == "__main__":
    main()
