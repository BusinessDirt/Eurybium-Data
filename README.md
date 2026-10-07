This Repo is used as a JSON file storage for the Minecraft mod https://github.com/BusinessDirt/Eurybium


## Runtime catalogs

The mod retrieves these files from one resolved commit on `master` and caches the validated revision:

- `patterns/chat.json` and `patterns/scoreboard.json`: `{ "schemaVersion": 1, "patterns": [{ "id": "stable.pattern.id", "pattern": "regex" }] }`. IDs are unique across both files.
- `mining/routes.json`: `{ "schemaVersion": 1, "routes": [] }`. Records contain a reserved route `id`, `island`, optional `region`/`mineshaft`, `space` (`WORLD` or `TEMPLATE`), and ordered `points` as integer `[x, y, z]` triples. Templates require a `layout` ID.
- `mining/nodes.json`: `{ "schemaVersion": 1, "nodes": [] }`. Records contain a unique `id`, the same location/space metadata, `kind` (`GEMSTONE`, `ORE`, `MITHRIL`), uppercase `material`, and a nonempty `blocks` list.

Built-in shaft routes use IDs such as `eurybium:JASP1` and `eurybium:JASPC`. Spawning IDs are `eurybium:SHAFT_SPAWN_MITHRIL`, `eurybium:SHAFT_SPAWN_TUNGSTEN`, and `eurybium:SHAFT_SPAWN_GEMSTONES`; these use island `DWARVEN_MINES` and region `DWARVEN_BASE_CAMP`. Use only reserved IDs defined by the mod. Empty catalogs indicate that surveyed data has not yet been added.

For the full contract and validation limits, see [the mod's repository data documentation](https://github.com/BusinessDirt/Eurybium/blob/master/docs/repository-data.md). Schema documents are not required; the mod validates updates before publication. Development-world ZIP release assets remain separate from runtime catalogs.


## Import Coleweight routes

Run from the repository root with Python 3.10+ (no additional dependencies):

```sh
python3 scripts/mineshaft_routes.py
```

At `Enter mineshaft route key: eurybium:`, enter a key such as `JASP1` or `JASPC` (a complete ID is also accepted). Then enter `clipboard` to read the copied Coleweight JSON directly on macOS, or `@/path/to/route.json` to import a file. You can also paste the JSON array on one or multiple lines. Clipboard/file imports are recommended for long routes because terminals can truncate long single-line pastes. The tool sorts waypoints by numeric `options.name`, converts them into ordered coordinate triples, and saves a world-space route to `mining/routes.json`. Colors and other Coleweight options are omitted because the mod controls their rendering.

It then asks for another key. Enter `q`, an empty key, or press Ctrl+C to finish; a blank line during a paste cancels that import. Existing route IDs require confirmation before replacement. Other routes are retained and invalid inputs leave the catalog unchanged.

The three `SHAFT_SPAWN_*` keys are also supported and receive the Dwarven Base Camp scope automatically. This tool assumes pasted coordinates are world coordinates; template routes still need an explicit layout and placement contract.

Run importer tests with `python3 -m unittest discover -s tests -v`.
