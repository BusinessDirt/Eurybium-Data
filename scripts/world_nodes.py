#!/usr/bin/env python3
"""Survey Java Edition Anvil worlds into face-connected mining node catalogs.

ZIPs are read directly, never extracted. Only candidate blocks are retained between chunks.
Use --help for examples; no third-party packages are required (Python 3.10+).
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import io
import json
from pathlib import Path, PurePosixPath
import re
import struct
import sys
import tempfile
from typing import BinaryIO, Iterator
from zipfile import BadZipFile, ZipFile
import zlib

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_CHUNK_BYTES = 16 * 1024 * 1024
MAX_REGION_BYTES = 64 * 1024 * 1024
MAX_NODE_BLOCKS = 65_536
MAX_TOTAL_BLOCKS = 8_000_000
MAX_NODE_FILES = 128
COLORS = ('white', 'orange', 'magenta', 'light_blue', 'yellow', 'lime', 'pink', 'gray',
          'light_gray', 'cyan', 'purple', 'blue', 'brown', 'green', 'red', 'black')
ORES = {14: 'gold_ore', 15: 'iron_ore', 16: 'coal_ore', 21: 'lapis_ore',
        56: 'diamond_ore', 73: 'redstone_ore', 74: 'redstone_ore',
        129: 'emerald_ore', 153: 'nether_quartz_ore'}
KINDS = {'GEMSTONE', 'ORE', 'MITHRIL'}
SHAFTS = {f'{name}{suffix}' for name in ('TOPA', 'SAPP', 'AMET', 'AMBE', 'JADE') for suffix in '12'}
SHAFTS |= {f'{name}{suffix}' for name in ('RUBY', 'ONYX', 'AQUA', 'CITR', 'PERI') for suffix in '12C'}
SHAFTS |= {'TITA1', 'UMBE1', 'TUNG1', 'FAIR1', 'JASP1', 'JASPC', 'OPAL1', 'OPALC', 'LITTL'}
ISLANDS = {'PRIVATE_ISLAND', 'PRIVATE_ISLAND_GUEST', 'HUB', 'DARK_AUCTION', 'WINTER',
           'THE_FARMING_ISLANDS', 'GARDEN', 'GARDEN_GUEST', 'GOLD_MINES', 'DEEP_CAVERNS',
           'DWARVEN_MINES', 'CRYSTAL_HOLLOWS', 'MINESHAFT', 'BACKWATER_BAYOU', 'LOTUS_ATOLL',
           'THE_PARK', 'GALATEA', 'TORRHUS_CANYON', 'SPIDER_DEN', 'THE_END', 'CRIMSON_ISLE',
           'THE_RIFT', 'CATACOMBS', 'DUNGEON_HUB', 'KUUDRA_ARENA', 'SAFARI'}
POSITION = tuple[int, int, int]


class NbtReader:
    """Small, bounded big-endian NBT reader; it does not interpret entities or other world data."""
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)

    def read(self, count: int) -> bytes:
        data = self.stream.read(count)
        if len(data) != count:
            raise ValueError('Truncated NBT data')
        return data

    def number(self, fmt: str):
        return struct.unpack('>' + fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        # Java uses modified UTF-8: encoded NUL and UTF-16 surrogate pairs need normalization.
        raw = self.read(self.number('H')).replace(b'\xc0\x80', b'\x00')
        text = raw.decode('utf-8', errors='surrogatepass')
        return text.encode('utf-16', errors='surrogatepass').decode('utf-16', errors='surrogatepass')

    def length(self) -> int:
        count = self.number('i')
        if not 0 <= count <= MAX_CHUNK_BYTES:
            raise ValueError('Invalid NBT array/list length')
        return count

    def payload(self, tag: int, depth: int = 0):
        if depth > 64:
            raise ValueError('NBT nesting exceeds 64 levels')
        if 1 <= tag <= 6:
            return self.number({1: 'b', 2: 'h', 3: 'i', 4: 'q', 5: 'f', 6: 'd'}[tag])
        if tag == 7:
            return self.read(self.length())
        if tag == 8:
            return self.string()
        if tag == 9:
            item_tag, count = self.number('B'), self.length()
            if item_tag == 0 and count:
                raise ValueError('Nonempty NBT End list')
            return [self.payload(item_tag, depth + 1) for _ in range(count)]
        if tag == 10:
            result = {}
            while (item_tag := self.number('B')) != 0:
                name = self.string()
                if name in result:
                    raise ValueError(f'Duplicate NBT tag {name}')
                result[name] = self.payload(item_tag, depth + 1)
            return result
        if tag in (11, 12):
            count = self.length()
            fmt = 'i' if tag == 11 else 'q'
            return list(struct.unpack(f'>{count}{fmt}', self.read(count * struct.calcsize(fmt))))
        raise ValueError(f'Unsupported NBT tag {tag}')

    def root(self) -> dict:
        if self.number('B') != 10:
            raise ValueError('Expected NBT compound root')
        self.string()
        return self.payload(10)


def decompress_chunk(data: bytes, compression: int) -> bytes:
    """Reject unsupported compression explicitly rather than silently skipping part of a survey."""
    if compression == 3:
        if len(data) > MAX_CHUNK_BYTES:
            raise ValueError('Chunk exceeds 16 MiB')
        return data
    if compression not in (1, 2):
        raise ValueError(f'Unsupported chunk compression {compression}; resave using zlib (LZ4 is not supported)')
    decoder = zlib.decompressobj(31 if compression == 1 else 15)
    result = decoder.decompress(data, MAX_CHUNK_BYTES + 1)
    if len(result) > MAX_CHUNK_BYTES or decoder.unconsumed_tail:
        raise ValueError('Chunk exceeds 16 MiB')
    if not decoder.eof:
        raise ValueError('Truncated compressed chunk')
    return result


class World:
    """Read one selected world root in a ZIP, or an extracted save directory."""
    def __init__(self, source: Path):
        self.source = source
        self.zip = None
        self.root = ''

    def __enter__(self):
        if self.source.is_dir():
            if not (self.source / 'level.dat').is_file():
                raise ValueError(f'{self.source} does not contain level.dat')
        else:
            self.zip = ZipFile(self.source)
            roots = [name.removesuffix('level.dat') for name in self.zip.namelist()
                     if PurePosixPath(name).name == 'level.dat' and not name.startswith('__MACOSX/')]
            if len(roots) != 1:
                self.zip.close()
                raise ValueError('ZIP must contain exactly one world with level.dat')
            self.root = roots[0]
        return self

    def __exit__(self, *args):
        if self.zip:
            self.zip.close()

    def regions(self) -> list[str]:
        if self.zip:
            return sorted(name[len(self.root):] for name in self.zip.namelist()
                          if name.startswith(self.root) and re.fullmatch(r'region/r\.-?\d+\.-?\d+\.mca', name[len(self.root):]))
        return sorted(str(p.relative_to(self.source)) for p in (self.source / 'region').glob('r.*.*.mca'))

    @contextmanager
    def open(self, name: str) -> Iterator[BinaryIO]:
        handle = self.zip.open(self.root + name) if self.zip else (self.source / name).open('rb')
        with handle:
            yield handle

    def chunks(self) -> Iterator[tuple[dict, int, int]]:
        regions = self.regions()
        if not regions:
            raise ValueError('No overworld Anvil region files found')
        for name in regions:
            rx, rz = map(int, PurePosixPath(name).name.split('.')[1:3])
            with self.open(name) as handle:
                region = handle.read(MAX_REGION_BYTES + 1)
            if not 8192 <= len(region) <= MAX_REGION_BYTES:
                raise ValueError(f'{name}: invalid region size (limit 64 MiB)')
            for slot in range(1024):
                location = int.from_bytes(region[slot * 4:slot * 4 + 4], 'big')
                if not location:
                    continue
                cx, cz = rx * 32 + slot % 32, rz * 32 + slot // 32
                offset, sectors = (location >> 8) * 4096, location & 255
                try:
                    if offset < 8192 or not sectors or offset + sectors * 4096 > len(region):
                        raise ValueError('Invalid chunk sector range')
                    length = int.from_bytes(region[offset:offset + 4], 'big')
                    if length < 1 or length + 4 > sectors * 4096:
                        raise ValueError('Invalid chunk length')
                    compression = region[offset + 4]
                    if compression & 128:
                        if length != 1:
                            raise ValueError('Invalid external chunk stub')
                        with self.open(f'region/c.{cx}.{cz}.mcc') as handle:
                            payload = handle.read(MAX_CHUNK_BYTES + 1)
                        if len(payload) > MAX_CHUNK_BYTES:
                            raise ValueError('External chunk exceeds 16 MiB')
                    else:
                        payload = region[offset + 5:offset + 4 + length]
                    root = NbtReader(decompress_chunk(payload, compression & 127)).root()
                    chunk = root.get('Level', root)
                    if chunk.get('xPos') != cx or chunk.get('zPos') != cz:
                        raise ValueError('Chunk coordinates do not match region slot')
                    yield root, cx, cz
                except (ValueError, OSError, KeyError, zlib.error) as error:
                    raise ValueError(f'{name}, chunk ({cx}, {cz}): {error}') from error


def nibble(data: bytes, index: int) -> int:
    return (data[index // 2] >> (4 * (index % 2))) & 15


def legacy_block(block_id: int, metadata: int) -> str:
    """Normalize relevant 1.8–1.12 IDs into modern names; non-candidates are ignored."""
    name = ORES.get(block_id)
    if block_id in (95, 160):
        name = COLORS[metadata] + ('_stained_glass' if block_id == 95 else '_stained_glass_pane')
    elif block_id in (35, 159, 171):
        name = COLORS[metadata] + {35: '_wool', 159: '_terracotta', 171: '_carpet'}[block_id]
    elif block_id == 168:
        name = ('prismarine', 'prismarine_bricks', 'dark_prismarine')[min(metadata, 2)]
    elif block_id == 1:
        name = {0: 'stone', 1: 'granite', 2: 'polished_granite', 3: 'diorite',
                4: 'polished_diorite', 5: 'andesite', 6: 'polished_andesite'}.get(metadata)
    else:
        name = name or {20: 'glass', 102: 'glass_pane', 80: 'snow_block', 82: 'clay',
                        172: 'terracotta', 179: 'red_sandstone', 173: 'coal_block', 42: 'iron_block',
                        41: 'gold_block', 49: 'obsidian', 152: 'redstone_block'}.get(block_id)
    return 'minecraft:' + name if name else ''


def section_blocks(section: dict, version: int) -> Iterator[tuple[int, str]]:
    """Decode YZX indexes, supporting old numeric IDs and both palette packing layouts."""
    if 'Blocks' in section:
        blocks, data, add = section['Blocks'], section.get('Data', bytes(2048)), section.get('Add')
        if len(blocks) != 4096 or len(data) != 2048 or (add is not None and len(add) != 2048):
            raise ValueError('Invalid legacy section arrays')
        for i, value in enumerate(blocks):
            if add is not None:
                value |= nibble(add, i) << 8
            yield i, legacy_block(value, nibble(data, i))
        return
    states = section.get('block_states')
    if states is None:
        if 'Palette' not in section:
            if 'BlockStates' in section:
                raise ValueError('Block states without palette')
            return  # Light/biome-only section.
        palette, data = section['Palette'], section.get('BlockStates', [])
    else:
        palette, data = states['palette'], states.get('data', [])
    if not 1 <= len(palette) <= 4096:
        raise ValueError('Invalid section palette')
    names = [item['Name'] for item in palette]
    if len(names) == 1 and not data:
        for i in range(4096):
            yield i, names[0]
        return
    bits = max(4, (len(names) - 1).bit_length())
    padded = version >= 2529
    per_long = 64 // bits
    expected = (4096 + per_long - 1) // per_long if padded else (4096 * bits + 63) // 64
    if len(data) != expected:
        raise ValueError(f'Invalid packed block state length: expected {expected}, got {len(data)}')
    longs = [value & ((1 << 64) - 1) for value in data]
    mask = (1 << bits) - 1
    for i in range(4096):
        word, shift = (i // per_long, (i % per_long) * bits) if padded else divmod(i * bits, 64)
        value = longs[word] >> shift
        # Before 1.16 an entry may span two longs; later versions pad each long instead.
        if not padded and shift + bits > 64:
            value |= longs[word + 1] << (64 - shift)
        index = value & mask
        if index >= len(names):
            raise ValueError('Block state index is outside palette')
        yield i, names[index]


@dataclass(frozen=True)
class Material:
    kind: str
    name: str


def classify(block: str, rules: dict[str, Material | None]) -> Material | None:
    if block in rules:
        return rules[block]
    if not block.startswith('minecraft:'):
        return None
    name = block.removeprefix('minecraft:')
    if name.endswith('_ore'):
        name = name.removeprefix('deepslate_').removeprefix('nether_').removesuffix('_ore')
        return Material('ORE', name.upper())
    if name == 'ancient_debris':
        return Material('ORE', 'ANCIENT_DEBRIS')
    for suffix in ('_stained_glass_pane', '_stained_glass'):
        if name.endswith(suffix):
            color = name.removesuffix(suffix)
            return Material('GEMSTONE', color.upper() + '_GLASS')
    if name in ('glass', 'glass_pane'):
        return Material('GEMSTONE', 'CLEAR_GLASS')
    return None


def load_rules(path: Path | None) -> dict[str, Material | None]:
    """Explicit IDs override defaults; null excludes a decorative material from the survey."""
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    rules = {}
    for block, entry in data.items():
        if not re.fullmatch(r'[a-z0-9_.-]+:[a-z0-9_./-]+', block) or len(block) > 128:
            raise ValueError(f'Invalid block ID {block}')
        if entry is None:
            rules[block] = None
        elif entry['kind'] in KINDS and re.fullmatch(r'[A-Z0-9_]{1,64}', entry['material']):
            rules[block] = Material(entry['kind'], entry['material'])
        else:
            raise ValueError(f'Invalid material rule for {block}')
    return rules


def scan_world(
    source: Path,
    rules: dict[str, Material | None],
    bounds: list[int] | None = None,
    *,
    allowed_kinds: set[str] | None = None,
) -> dict[POSITION, tuple[Material, str]]:
    """Retain only relevant materials before clustering and applying the candidate-block limit."""
    blocks = {}
    materials = {}
    entries = {}

    def material_for(block: str) -> Material | None:
        if block not in materials:
            material = classify(block, rules)
            materials[block] = material if material is not None and (allowed_kinds is None or material.kind in allowed_kinds) else None
        return materials[block]

    with World(source) as world:
        for root, cx, cz in world.chunks():
            chunk = root.get('Level', root)
            sections = chunk.get('sections', chunk.get('Sections', []))
            for section in sections:
                # Most sections contain no candidate blocks. Avoid decoding 4096 air/stone entries.
                palette = section.get('block_states', {}).get('palette', section.get('Palette'))
                if palette is not None and not any(material_for(item['Name']) for item in palette):
                    continue
                sy = section['Y'] * 16
                for i, block in section_blocks(section, root.get('DataVersion', 0)):
                    material = material_for(block)
                    if material is None:
                        continue
                    pos = cx * 16 + (i & 15), sy + (i >> 8), cz * 16 + ((i >> 4) & 15)
                    if bounds and not all(bounds[a] <= pos[a] <= bounds[a + 3] for a in range(3)):
                        continue
                    if pos in blocks:
                        raise ValueError(f'Duplicate saved block coordinate {pos}')
                    # Reuse one value tuple per block type rather than allocating it per coordinate.
                    if block not in entries:
                        entries[block] = material, block
                    blocks[pos] = entries[block]
                    if len(blocks) > MAX_TOTAL_BLOCKS:
                        raise ValueError(f'Survey exceeds {MAX_TOTAL_BLOCKS:,} candidate blocks; narrow it using --bounds')
    return blocks


def cluster_blocks(
    blocks: dict[POSITION, tuple[Material, str]],
    world_id: str,
    origin: POSITION = (0, 0, 0),
    *,
    consume: bool = False,
) -> list[dict]:
    """Flood-fill each face-connected component once; sorting makes output and IDs deterministic."""
    # The CLI owns its scan result and can release visited entries; callers retain copy semantics.
    remaining = blocks if consume else dict(blocks)
    nodes = []
    for start in sorted(blocks):
        if start not in remaining:
            continue
        material, block_type = remaining.pop(start)
        stack, members, types = [start], [], {block_type}
        while stack:
            pos = stack.pop()
            members.append(pos if origin == (0, 0, 0) else tuple(pos[i] - origin[i] for i in range(3)))
            x, y, z = pos
            for neighbor in ((x - 1, y, z), (x + 1, y, z), (x, y - 1, z),
                             (x, y + 1, z), (x, y, z - 1), (x, y, z + 1)):
                candidate = remaining.get(neighbor)
                if candidate is not None and candidate[0] == material:
                    types.add(remaining.pop(neighbor)[1])
                    stack.append(neighbor)
        if len(types) > 64:
            raise ValueError('Cluster exceeds 64 block types; refine material rules')
        if len(members) > MAX_NODE_BLOCKS:
            raise ValueError(f'Cluster at {start} has {len(members)} blocks (limit {MAX_NODE_BLOCKS}); refine the rules/bounds')
        members.sort()
        if any(not -(2**31) <= value < 2**31 for point in members for value in point):
            raise ValueError('Output coordinates exceed signed 32-bit integers')
        anchor = '_'.join(map(str, members[0]))
        node_id = f'{world_id}/{material.name.lower()}/{anchor}'
        if len(node_id) > 128:
            raise ValueError('Node ID exceeds 128 characters; shorten the world ID or material name')
        nodes.append({'id': node_id, 'kind': material.kind,
                      'material': material.name, 'blockTypes': sorted(types), 'blocks': members})
    return nodes


def json_text(data: dict) -> str:
    """One coordinate triple per line keeps large node lists practical to review."""
    text = json.dumps(data, indent=2) + '\n'
    text = re.sub(r'\[\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\s*\]', r'[\1, \2, \3]', text)
    if len(text.encode('utf-8')) > MAX_FILE_BYTES:
        raise ValueError('JSON output exceeds the per-file limit')
    return text


def survey_files(world_id: str, scope: dict, nodes: list[dict]) -> list[tuple[str, str]]:
    """Split on whole-node boundaries, retaining the usual filename for a small survey."""
    header = json.dumps({'schemaVersion': 1, **scope}, indent=2)[:-2] + ',\n  "nodes": [\n'
    footer = '\n  ]\n}\n'
    overhead = len((header + footer).encode('utf-8'))
    parts, records, size = [], [], overhead
    for node in nodes:
        text = json_text(node).rstrip()
        text = '\n'.join('    ' + line for line in text.splitlines())
        node_size = len(text.encode('utf-8'))
        if overhead + node_size > MAX_FILE_BYTES:
            raise ValueError(f'Node {node["id"]} alone exceeds the per-file limit')
        if records and (len(records) >= 20_000 or size + 2 + node_size > MAX_FILE_BYTES):
            parts.append(header + ',\n'.join(records) + footer)
            records, size = [], overhead
        size += node_size + (2 if records else 0)
        records.append(text)
    parts.append(header + ',\n'.join(records) + footer)
    if len(parts) == 1:
        return [(f'{world_id}.json', parts[0])]
    if len(world_id) > 55:
        raise ValueError('Split survey IDs must be at most 55 characters')
    return [(f'{world_id}-part-{i:03}.json', text) for i, text in enumerate(parts, start=1)]


def survey_path(path: str, world_id: str) -> bool:
    """Recognize only this export's base filename and numeric parts when updating its index."""
    return path == f'mining/nodes/{world_id}.json' or re.fullmatch(
        rf'mining/nodes/{re.escape(world_id)}-part-\d{{3}}\.json', path,
    ) is not None


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def make_scope(args, world_id: str) -> dict:
    scope = {'island': args.island, 'space': args.space}
    if args.region:
        scope['region'] = args.region
    shaft = args.mineshaft or (world_id.upper() if args.island == 'MINESHAFT' else None)
    if shaft:
        shaft = shaft.removeprefix('eurybium:').upper()
        if shaft not in SHAFTS or args.island != 'MINESHAFT':
            raise ValueError('Use a known mineshaft key (e.g. JASP1) with island MINESHAFT')
        scope['mineshaft'] = shaft
    if args.space == 'TEMPLATE':
        if not args.layout:
            raise ValueError('TEMPLATE data requires --layout')
        scope['layout'] = args.layout
    elif args.layout or args.origin != [0, 0, 0]:
        raise ValueError('--layout and --origin require --space TEMPLATE')
    for key in ('region', 'layout'):
        if key in scope and not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', scope[key]):
            raise ValueError(f'Invalid {key} ID')
    return scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                    epilog='Examples:\n  world_nodes.py JASP1=dev/worlds/jasper-mineshaft.zip --island MINESHAFT\n'
                                           '  world_nodes.py tunnels=dev/worlds/glacite-tunnels.zip --island DWARVEN_MINES --region GLACITE_TUNNELS')
    parser.add_argument('worlds', nargs='+', help='ZIP/save paths, optionally prefixed with ID= (shaft IDs for mineshafts)')
    parser.add_argument('--island', required=True, choices=sorted(ISLANDS))
    parser.add_argument('--mineshaft', help='Explicit shaft key for a single input; otherwise taken from ID')
    parser.add_argument('--region', help='Optional region ID, e.g. GLACITE_TUNNELS')
    parser.add_argument('--space', choices=('WORLD', 'TEMPLATE'), default='WORLD')
    parser.add_argument('--layout', help='Template placement/layout ID')
    parser.add_argument('--origin', type=int, nargs=3, default=[0, 0, 0], metavar=('X', 'Y', 'Z'), help='Subtract this origin for TEMPLATE coordinates')
    parser.add_argument('--bounds', type=int, nargs=6, metavar=('MIN_X', 'MIN_Y', 'MIN_Z', 'MAX_X', 'MAX_Y', 'MAX_Z'), help='Inclusive survey box (may cut nodes at its edges)')
    parser.add_argument('--rules', type=Path, help='JSON mapping Minecraft IDs to {kind, material}, or null to exclude')
    parser.add_argument('--output-dir', type=Path, default=REPO_ROOT / 'mining/nodes', help='JSON surveys, split into bounded files when needed')
    parser.add_argument('--no-index', action='store_true', help='Do not register files in the adjacent mining/nodes.json index')
    args = parser.parse_args(argv)
    try:
        if args.mineshaft and len(args.worlds) != 1:
            raise ValueError('--mineshaft supports a single input; use ID=PATH for multiple shafts')
        if args.bounds and any(args.bounds[i] > args.bounds[i + 3] for i in range(3)):
            raise ValueError('Bounds minima must not exceed maxima')
        rules = load_rules(args.rules)
        output = args.output_dir.resolve()
        if not args.no_index and output.name != 'nodes':
            raise ValueError('Indexed output directory must be named nodes (or use --no-index)')
        index_path = output.parent / 'nodes.json'
        index = json.loads(index_path.read_text(encoding='utf-8')) if not args.no_index and index_path.exists() else {'schemaVersion': 1, 'files': []}
        if index.get('schemaVersion') != 1 or not isinstance(index.get('files', []), list):
            raise ValueError('Invalid node index')
        ids, pending, world_ids = set(), [], []
        for value in args.worlds:
            if '=' in value:
                world_id, path = value.split('=', 1)
            else:
                path = value
                world_id = re.sub(r'[^a-zA-Z0-9_-]+', '-', Path(path).stem).strip('-')
            world_id = world_id.removeprefix('eurybium:')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', world_id) or world_id.lower() in ids:
                raise ValueError(f'Invalid or duplicate world ID {world_id}')
            ids.add(world_id.lower())
            world_ids.append(world_id)
            scope = make_scope(args, world_id)
            source = Path(path).expanduser()
            print(f'Reading {source} …', flush=True)
            # Mineshaft catalogs are for gemstone routes; other islands keep every supported kind.
            allowed_kinds = {'GEMSTONE'} if scope['island'] == 'MINESHAFT' else None
            blocks = scan_world(source, rules, args.bounds, allowed_kinds=allowed_kinds)
            count = len(blocks)
            nodes = cluster_blocks(blocks, world_id, tuple(args.origin), consume=True)
            parts = survey_files(world_id, scope, nodes)
            pending.extend((output / name, text) for name, text in parts)
            # Drop coordinate lists before processing another world; output parts now own the data.
            print(f'  {count:,} blocks → {len(nodes):,} clusters in {len(parts)} file(s); {dict(Counter(n["material"] for n in nodes))}')
            del nodes, blocks, parts
        if not args.no_index:
            files = {path for path in index.get('files', []) if not any(survey_path(path, name) for name in world_ids)}
            files |= {f'mining/nodes/{p.name}' for p, _ in pending}
            if len(files) > MAX_NODE_FILES or any(not re.fullmatch(r'mining/nodes/[A-Za-z0-9_-]{1,64}\.json', p) for p in files):
                raise ValueError('Invalid index file paths or more than 128 files')
            index['files'] = sorted(files)
            index_text = json_text(index)
        # Validate the whole requested batch before replacing any file; each write is atomic.
        for path, text in pending:
            atomic_write(path, text)
            print(f'Wrote {path}')
        if not args.no_index:
            atomic_write(index_path, index_text)
        return 0
    except (ValueError, OSError, KeyError, IndexError, TypeError, struct.error, BadZipFile) as error:
        print(f'Could not survey worlds: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
