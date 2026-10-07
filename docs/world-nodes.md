# World node surveys

Run from the data repository root with Python 3.10+. No dependencies or running Minecraft client are required. ZIP archives are read directly and never extracted or modified; extracted save directories also work. A ZIP may contain either `level.dat` at its root or a single wrapper folder like `Example/level.dat`.

```sh
python3 scripts/world_nodes.py \
  JASP1=dev/worlds/jasper-mineshaft.zip \
  JASPC=dev/worlds/jasper-crystal-mineshaft.zip \
  --island MINESHAFT

python3 scripts/world_nodes.py \
  glacite-tunnels=dev/worlds/glacite-tunnels.zip \
  --island DWARVEN_MINES --region GLACITE_TUNNELS

python3 scripts/world_nodes.py \
  crystal-hollows=dev/worlds/clean-crystal-hollows.zip \
  --island CRYSTAL_HOLLOWS
```

`ID=PATH` chooses a filename and prefixes node IDs. For mineshafts, the ID is also the shaft key, matching the mod's registry (`JASP1`, `JASPC`, etc.). Filenames alone cannot reliably identify a shaft variant. For a single input you can instead provide `--mineshaft JASP1`. Other inputs may omit `ID=` and use the archive/save name. Quote arguments containing spaces. All inputs in one invocation share island/region/space; process different islands separately.

The default output is `mining/nodes/<ID>.json`, and the adjacent `mining/nodes.json` index receives a sorted list of referenced files. Existing surveys with the same ID are replaced; other index entries are retained. The requested batch is processed before writing, so an invalid world leaves its batch untouched. Each file replacement is atomic; commit the survey files and index together. A disk failure during writing may leave a partial batch on disk, which the runtime loader will reject if incomplete. For experiments use `--output-dir /tmp/survey/nodes`; use `--no-index` for standalone output in any directory. JSON is deterministic, one block coordinate per line, and an unchanged survey produces no diff.

## Clustering and materials

A node is a maximal connected component with a single `(kind, material)` key. Connectivity means sharing one of six faces: diagonal and corner contact do not join clusters. Membership joins across section, chunk, and region boundaries. Glass blocks and panes of the same color share a key. Stone/deepslate variants of an ore share its ore key. Block properties such as pane attachments and redstone lighting do not split a cluster. A node ID combines world ID, material, and its smallest coordinate, making it stable when unrelated nodes are added; changing that anchor or merging/splitting clusters can change IDs.

Defaults include vanilla ores, ancient debris, clear glass, and all stained-glass colors. Glass materials use explicit physical names like `MAGENTA_GLASS`; the tool does not assume every colored window is a gemstone. Use block rules to translate known colors to gemstone names, include Hypixel blocks such as mithril, or exclude decoration:

```json
{
  "minecraft:magenta_stained_glass": { "kind": "GEMSTONE", "material": "JASPER" },
  "minecraft:magenta_stained_glass_pane": { "kind": "GEMSTONE", "material": "JASPER" },
  "minecraft:prismarine": { "kind": "MITHRIL", "material": "MITHRIL" },
  "minecraft:glass": null,
  "minecraft:glass_pane": null
}
```

Save that mapping to a JSON file and supply `--rules /path/to/rules.json`. Entries override defaults. Blocks with matching custom `(kind, material)` join even if their registry IDs differ; only map blocks together when they really represent the same node. Unlisted blocks retain defaults. Accepted kinds are `GEMSTONE`, `ORE`, and `MITHRIL`; materials use 1–64 uppercase letters, digits, or underscores. Old numeric block IDs are normalized to modern names for ores/glass and common wool, clay, stone, prismarine, and metal blocks, so the same rules work on legacy vanilla saves. Unknown legacy/modded numeric IDs are excluded rather than guessed.

`--bounds MIN_X MIN_Y MIN_Z MAX_X MAX_Y MAX_Z` restricts the survey to an inclusive world-space box. It can remove decoration and keep large saves manageable. Bounds can cut nodes at their edges: review boundary clusters before publishing. Without bounds, the tool surveys all saved overworld chunks, including unrelated vanilla ores around a downloaded build. It cannot reconstruct blocks absent from a partial download or identify nonphysical Hypixel resource semantics by itself.

## JSON structure

Index:

```json
{
  "schemaVersion": 1,
  "files": ["mining/nodes/JASP1.json"]
}
```

Per-world survey (coordinates below are illustrative):

```json
{
  "schemaVersion": 1,
  "island": "MINESHAFT",
  "mineshaft": "JASP1",
  "space": "WORLD",
  "nodes": [{
    "id": "JASP1/magenta_glass/10_100_20",
    "kind": "GEMSTONE",
    "material": "MAGENTA_GLASS",
    "blockTypes": ["minecraft:magenta_stained_glass", "minecraft:magenta_stained_glass_pane"],
    "blocks": [[10, 100, 20], [11, 100, 20]]
  }]
}
```

Scope appears once per file. `blockTypes` preserves the Minecraft IDs actually observed in that cluster. There are no stored centers or bounding boxes to drift out of sync. Kotlin exposes immutable nodes through `RepoAPI.snapshot.nodes`; all indexed files participate in the existing commit-pinned download, cache, validation, and `RepoUpdateEvent` lifecycle.

World-space coordinates are the default. For reusable layouts use `--space TEMPLATE --layout jasper-layout --origin X Y Z`: subtracting the origin produces local integer positions. This only exports coordinates; it does not establish how a live world translates or rotates that template. Consumers must resolve a verified placement before rendering it. In particular, a surveyed Crystal Hollows save does not prove the same coordinates apply to other generated lobbies.

## Supported formats, performance, and limits

The reader supports Java Edition Anvil `.mca` data: legacy numeric sections (1.8–1.12), 1.13–1.17 palettes, and modern lowercase `block_states`. It handles contiguous pre-1.16 and padded modern bit packing, negative coordinates/section heights, gzip/zlib/raw chunks, and external `.mcc` chunks. LZ4 compression, Bedrock/LevelDB, pre-Anvil `.mcr`, and other dimensions are not supported and require conversion or a separate tool. Unsupported compression/corrupt chunk data aborts the survey rather than producing silently incomplete output.

Only candidate blocks are retained across chunks. Palette sections without candidates are skipped, and material classifications are reused. Scanning costs scale with saved chunk data; flood filling uses six neighbor checks per candidate, plus sorting for stable output. Memory scales with candidates and the largest region (limit 64 MiB), rather than storing the entire world. The tool copies its candidate map during clustering and builds JSON in memory; this is deliberately simple and bounded rather than a streaming world database.

Limits match the Kotlin catalog contract: 8 MiB per JSON file, 20,000 nodes per file, 65,536 blocks per node, 1,000,000 blocks across a runtime snapshot, 128 indexed files, 24 MiB combined repository source data, and signed 32-bit coordinates. Limits per survey are checked before writing. The combined catalog limits are enforced by Kotlin; do not assume that multiple individually valid surveys fit together. Survey limits are distinct from the planned 2,048-block **per-frame render budget**; storing a large cluster does not authorize rendering every block in it.

Run tests with `python3 -m unittest discover -s tests -v`. Tests include synthetic compressed ZIPs, external chunks, legacy IDs, both palette layouts, face connectivity across regions, diagonal separation, template origin, deterministic output, malformed data, and CLI/index behavior. Review generated data before committing it.
