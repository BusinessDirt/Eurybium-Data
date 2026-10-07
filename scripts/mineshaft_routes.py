#!/usr/bin/env python3
"""Interactively convert Coleweight routes into the mod's repository route catalog."""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROUTES_FILE = Path(__file__).resolve().parent.parent / "mining" / "routes.json"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_POINTS = 10_000
MINESHAFT_KEYS = frozenset(['TOPA1', 'TOPA2', 'SAPP1', 'SAPP2', 'AMET1', 'AMET2', 'AMBE1', 'AMBE2', 'JADE1', 'JADE2', 'TITA1', 'UMBE1', 'TUNG1', 'FAIR1', 'RUBY1', 'RUBY2', 'RUBYC', 'ONYX1', 'ONYX2', 'ONYXC', 'AQUA1', 'AQUA2', 'AQUAC', 'CITR1', 'CITR2', 'CITRC', 'PERI1', 'PERI2', 'PERIC', 'JASP1', 'JASPC', 'OPAL1', 'OPALC', 'LITTL'])
SPAWNING_KEYS = frozenset({"SHAFT_SPAWN_MITHRIL", "SHAFT_SPAWN_TUNGSTEN", "SHAFT_SPAWN_GEMSTONES"})


def normalize_key(value: str) -> str:
    """Accept a bare key or full ID and normalize it to a registered route key."""
    key = value.strip()
    if key.lower().startswith("eurybium:"):
        key = key[len("eurybium:"):]
    key = key.upper()
    if key not in MINESHAFT_KEYS | SPAWNING_KEYS:
        raise ValueError(f"Unknown route key '{key}'. Use a shaft key such as JASP1 or JASPC.")
    return key


def convert_route(key: str, waypoints: object) -> dict:
    """Sort by Coleweight's numeric options.name; retain only block positions and route scope."""
    key = normalize_key(key)
    if not isinstance(waypoints, list) or not 1 <= len(waypoints) <= MAX_POINTS:
        raise ValueError(f"The Coleweight route must be an array of 1–{MAX_POINTS:,} waypoints.")
    numbered_points = []
    for index, waypoint in enumerate(waypoints, start=1):
        if not isinstance(waypoint, dict):
            raise ValueError(f"Waypoint {index} must be an object.")
        point = [waypoint.get(axis) for axis in ("x", "y", "z")]
        if any(type(value) is not int or not -(2**31) <= value < 2**31 for value in point):
            raise ValueError(f"Waypoint {index} needs integer x, y, and z coordinates within the Int range.")
        options = waypoint.get("options")
        name = options.get("name") if isinstance(options, dict) else None
        if (not isinstance(name, str) or not re.fullmatch(r"[+-]?[0-9]+", name)) and not isinstance(name, int):
            raise ValueError(f"Waypoint {index} needs an integer string in options.name.")
        number = int(name)
        if not -(2**31) <= number < 2**31:
            raise ValueError(f"Waypoint {index} has an out-of-range options.name.")
        numbered_points.append((number, point))

    # Stable sorting matches the mod's import, including repeated numbers and sparse numbering.
    points = [point for _, point in sorted(numbered_points, key=lambda item: item[0])]
    route = {"id": f"eurybium:{key}"}
    if key in SPAWNING_KEYS:
        route.update(island="DWARVEN_MINES", region="DWARVEN_BASE_CAMP")
    else:
        route.update(island="MINESHAFT", mineshaft=key)
    route.update(space="WORLD", points=points)
    return route


def load_catalog(path: Path) -> dict:
    """Read existing data without replacing malformed files with an empty catalog."""
    if not path.exists():
        return {"schemaVersion": 1, "routes": []}
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("The route catalog exceeds the mod's 8 MiB file limit.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
        raise ValueError("The route catalog must be an object with schemaVersion 1.")
    routes = data.get("routes")
    if not isinstance(routes, list) or any(not isinstance(route, dict) or not isinstance(route.get("id"), str) for route in routes):
        raise ValueError("The route catalog must contain a routes array with an ID on every entry.")
    if len({route["id"] for route in routes}) != len(routes):
        raise ValueError("The existing catalog contains duplicate route IDs; fix these before importing.")
    return data


def save_route(path: Path, route: dict, *, replace: bool = False) -> None:
    """Re-read the catalog and atomically add/replace one entry, retaining other records and metadata."""
    data = load_catalog(path)
    routes = data["routes"]
    existing = next((index for index, entry in enumerate(routes) if entry["id"] == route["id"]), None)
    if existing is not None:
        if not replace:
            raise ValueError(f"Route {route['id']} already exists; replacement was not confirmed.")
        routes[existing] = route
    else:
        if len(routes) >= 20_000:
            raise ValueError("The catalog already has the mod's maximum of 20,000 route records.")
        routes.append(route)
    text = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("Adding this route would exceed the mod's 8 MiB file limit.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_route_bytes(content: bytes) -> object:
    """Apply the same size limit to clipboard and file input before JSON decoding."""
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("Route input exceeds 8 MiB.")
    return json.loads(content.decode("utf-8-sig"))


def read_clipboard() -> object:
    """Read macOS clipboard data directly, bypassing terminal line-length limits."""
    if sys.platform != "darwin":
        raise ValueError("Clipboard import currently supports macOS. Use @/path/to/route.json instead.")
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, check=True, timeout=10)
    except subprocess.SubprocessError as error:
        raise ValueError("Could not read the clipboard. Copy the Coleweight JSON again or use a file.") from error
    return parse_route_bytes(result.stdout)


def read_route_file(value: str) -> object:
    """Read a file path entered after @; spaces and optionally paired quotes are accepted."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not value:
        raise ValueError("Enter a JSON file path after @.")
    with Path(value).expanduser().open("rb") as file:
        return parse_route_bytes(file.read(MAX_FILE_BYTES + 1))


def read_route() -> object | None:
    """Read pasted JSON, clipboard data, or a file; no trailing blank line is needed."""
    print("Enter clipboard (recommended for long routes), @/path/to/route.json, or paste JSON.")
    print("Multiline JSON is supported; a blank line cancels.")
    lines = []
    depth = 0
    quoted = False
    escaped = False
    size = 0
    while True:
        line = input()
        if not line.strip():
            return None
        if not lines:
            source = line.strip()
            if source.lower() == "clipboard":
                return read_clipboard()
            if source.startswith("@"):
                return read_route_file(source[1:])
        if not lines and not line.lstrip().startswith("["):
            raise ValueError("Paste the Coleweight JSON array, starting with '['.")
        lines.append(line)
        size += len(line.encode("utf-8")) + 1
        if size > MAX_FILE_BYTES:
            raise ValueError("Pasted route exceeds 8 MiB.")
        # Only count brackets outside JSON strings, so labels/options cannot end the paste early.
        for char in line:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
        if depth <= 0 and not quoted:
            return json.loads("\n".join(lines))


def main() -> None:
    print("Add mining routes. Enter a bare key or full ID; q or an empty key quits.")
    print(f"Saving to {ROUTES_FILE}")
    while True:
        try:
            value = input("Enter mineshaft route key: eurybium:").strip()
            if not value or value.lower() in {"q", "quit", "exit"}:
                return
            key = normalize_key(value)
            pasted = read_route()
            if pasted is None:
                print("Import cancelled.\n")
                continue
            route = convert_route(key, pasted)
            exists = any(entry["id"] == route["id"] for entry in load_catalog(ROUTES_FILE)["routes"])
            if exists and input(f"{route['id']} already exists. Replace it? [y/N]: ").strip().lower() not in {"y", "yes"}:
                print("Existing route kept.\n")
                continue
            save_route(ROUTES_FILE, route, replace=exists)
            print(f"Saved {route['id']} ({len(route['points'])} waypoints).\n")
        except (EOFError, KeyboardInterrupt):
            print("\nDone.")
            return
        except (ValueError, OSError) as error:
            print(f"Could not import route: {error}\n")


if __name__ == "__main__":
    main()
