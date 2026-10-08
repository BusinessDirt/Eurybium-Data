This Repo is used as a JSON file storage for the Minecraft mod https://github.com/BusinessDirt/Eurybium


## Runtime catalogs

The mod retrieves these files from one resolved commit on `master` and caches the validated revision:

- `patterns/chat.json` and `patterns/scoreboard.json`: `{ "schemaVersion": 1, "patterns": [{ "id": "stable.pattern.id", "pattern": "regex" }] }`. IDs are unique across both files.
- `mining/routes.json`: `{ "schemaVersion": 1, "routes": [] }`. Records contain a reserved route `id`, `island`, optional `region`/`mineshaft`, `space` (`WORLD` or `TEMPLATE`), and ordered `points` as `[x, y, z]` or `[x, y, z, "nodeMaterial"]`. The optional material is a resource name or Minecraft block ID (for example `minecraft:magenta_stained_glass` for Jasper). Omitted, blank, or null values choose the nearest allowed resource. Gemstone shaft waypoints name their intended gemstone; mixed spawning waypoints leave it omitted. Templates require a `layout` ID.
- `mining/nodes.json`: `{ "schemaVersion": 1, "files": [] }`. Lists per-world surveys such as `mining/nodes/JASP1.json`. Runtime surveys use one known mineshaft file (for example `JASP1.json`), `island: MINESHAFT`, `space: WORLD`, and `kind: GEMSTONE`. Nodes contain unique `id`, uppercase `material`, Minecraft glass `blockTypes`, and integer `blocks`.

Built-in shaft routes use IDs such as `eurybium:JASP1` and `eurybium:JASPC`. Spawning IDs are `eurybium:SHAFT_SPAWN_MITHRIL`, `eurybium:SHAFT_SPAWN_TUNGSTEN`, and `eurybium:SHAFT_SPAWN_GEMSTONES`; these use island `DWARVEN_MINES` and region `DWARVEN_BASE_CAMP`. Use only reserved IDs defined by the mod. Empty catalogs indicate that surveyed data has not yet been added.

For the full contract and validation limits, see [the world-node guide](docs/world-nodes.md). Schema documents are not required; the mod validates updates before publication. Development-world ZIP release assets remain separate from runtime catalogs.


## Import Coleweight routes

Run from the repository root with Python 3.10+ (no additional dependencies):

```sh
python3 scripts/mineshaft_routes.py
```

At `Enter mineshaft route key: eurybium:`, enter a key such as `JASP1` or `JASPC` (a complete ID is also accepted). Then enter `clipboard` to read the copied Coleweight JSON directly on macOS, or `@/path/to/route.json` to import a file. You can also paste the JSON array on one or multiple lines. Clipboard/file imports are recommended for long routes because terminals can truncate long single-line pastes. The tool sorts waypoints by numeric `options.name`, converts them into ordered coordinate triples, and saves a world-space route to `mining/routes.json`. Colors and other Coleweight options are omitted because the mod controls their rendering.

It then asks for another key. Enter `q`, an empty key, or press Ctrl+C to finish; a blank line during a paste cancels that import. Existing route IDs require confirmation before replacement. Other routes are retained and invalid inputs leave the catalog unchanged.

The three `SHAFT_SPAWN_*` keys are also supported and receive the Dwarven Base Camp scope automatically. This tool assumes pasted coordinates are world coordinates; template routes still need an explicit layout and placement contract.

Run importer tests with `python3 -m unittest discover -s tests -v`.


## Survey world node clusters

Use Python 3.10+ without additional dependencies:

```sh
python3 scripts/world_nodes.py JASP1=dev/worlds/jasper-mineshaft.zip --island MINESHAFT
```

This writes `mining/nodes/JASP1.json` and registers it in `mining/nodes.json`. Large worlds automatically produce numbered part files without splitting clusters. Multiple `ID=PATH` inputs are supported. The mod consumes only gemstone mineshaft surveys. The general exporter can still process other islands for offline experiments; those exports are outside the runtime catalog. See [the world-node guide](docs/world-nodes.md) for material rules, template coordinates, output format, and limits.
