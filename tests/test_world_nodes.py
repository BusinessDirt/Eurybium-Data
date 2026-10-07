"""Format fixtures are built locally; tests do not need downloads or Minecraft running."""
import gzip
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/world_nodes.py'
spec = importlib.util.spec_from_file_location('world_nodes', SCRIPT)
w = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = w
spec.loader.exec_module(w)


def string(value):
    data = value.encode('utf-8')
    return struct.pack('>H', len(data)) + data


def payload(tag, value):
    if tag in (1, 3, 4):
        return struct.pack('>' + {1: 'b', 3: 'i', 4: 'q'}[tag], value)
    if tag == 7:
        return struct.pack('>i', len(value)) + value
    if tag == 8:
        return string(value)
    if tag == 9:
        item_tag, items = value
        return bytes([item_tag]) + struct.pack('>i', len(items)) + b''.join(payload(item_tag, item) for item in items)
    if tag == 10:
        return b''.join(bytes([t]) + string(name) + payload(t, v) for name, (t, v) in value.items()) + b'\0'
    if tag == 12:
        return struct.pack('>i', len(value)) + b''.join(struct.pack('>Q', v & ((1 << 64) - 1)) for v in value)
    raise AssertionError(tag)


def packed(values, bits, padded):
    per_long = 64 // bits
    size = (4096 + per_long - 1) // per_long if padded else (4096 * bits + 63) // 64
    result = [0] * size
    for i, value in enumerate(values):
        word, shift = (i // per_long, (i % per_long) * bits) if padded else divmod(i * bits, 64)
        result[word] |= value << shift
        if not padded and shift + bits > 64:
            result[word + 1] |= value >> (64 - shift)
    return [v & ((1 << 64) - 1) for v in result]


def chunk(cx, cz, positions, version=3000):
    values = [0] * 4096
    for index in positions:
        values[index] = 1
    section = {'Y': (1, -1), 'block_states': (10, {
        'palette': (9, (10, [{'Name': (8, 'minecraft:air')}, {'Name': (8, 'minecraft:red_stained_glass')}])),
        'data': (12, packed(values, 4, True)),
    })}
    root = {'DataVersion': (3, version), 'xPos': (3, cx), 'zPos': (3, cz), 'sections': (9, (10, [section]))}
    return b'\x0a\0\0' + payload(10, root)


def region(chunks, compression=2, external=False):
    header, body = bytearray(8192), bytearray()
    external_files = {}
    for (cx, cz), data in chunks.items():
        compressed = {1: gzip.compress, 2: zlib.compress, 3: lambda x: x}[compression](data)
        if external:
            external_files[f'c.{cx}.{cz}.mcc'] = compressed
            compressed = b''
        record = struct.pack('>I', len(compressed) + 1) + bytes([compression | (128 if external else 0)]) + compressed
        sectors = (len(record) + 4095) // 4096
        slot = (cx % 32) + (cz % 32) * 32
        offset = 2 + len(body) // 4096
        header[slot * 4:slot * 4 + 4] = ((offset << 8) | sectors).to_bytes(4, 'big')
        body.extend(record + bytes(sectors * 4096 - len(record)))
    return bytes(header + body), external_files


class WorldNodesTest(unittest.TestCase):
    def test_face_connected_materials_cross_boundaries_without_diagonal_merging(self):
        red, blue = w.Material('GEMSTONE', 'RED_GLASS'), w.Material('GEMSTONE', 'BLUE_GLASS')
        blocks = {(15, -1, 0): (red, 'minecraft:red_stained_glass'),
                  (16, -1, 0): (red, 'minecraft:red_stained_glass_pane'),
                  (17, 0, 0): (red, 'minecraft:red_stained_glass'),
                  (16, -1, 1): (blue, 'minecraft:blue_stained_glass')}
        nodes = w.cluster_blocks(blocks, 'test')
        self.assertEqual([2, 1, 1], [len(n['blocks']) for n in nodes])
        self.assertEqual(2, len(nodes[0]['blockTypes']))
        self.assertEqual(nodes, w.cluster_blocks(dict(reversed(list(blocks.items()))), 'test'))
        self.assertEqual(4, len(blocks))  # Clustering does not consume the caller's input.

    def test_template_origin_and_stable_ids(self):
        red = w.Material('GEMSTONE', 'RED_GLASS')
        nodes = w.cluster_blocks({(-10, 50, 20): (red, 'minecraft:red_stained_glass')}, 'JASP1', (-10, 50, 20))
        self.assertEqual([(0, 0, 0)], nodes[0]['blocks'])
        self.assertEqual('JASP1/red_glass/0_0_0', nodes[0]['id'])

    def test_ore_variants_glass_panes_and_rule_overrides(self):
        self.assertEqual(w.classify('minecraft:iron_ore', {}), w.classify('minecraft:deepslate_iron_ore', {}))
        self.assertEqual(w.classify('minecraft:red_stained_glass', {}), w.classify('minecraft:red_stained_glass_pane', {}))
        self.assertIsNone(w.classify('minecraft:stone', {}))
        self.assertIsNone(w.classify('minecraft:glass', {'minecraft:glass': None}))
        mithril = w.Material('MITHRIL', 'MITHRIL')
        self.assertEqual(mithril, w.classify('minecraft:prismarine', {'minecraft:prismarine': mithril}))

    def test_numeric_legacy_ids_colors_and_add_nibbles(self):
        blocks, data, add = bytearray(4096), bytearray(2048), bytearray(2048)
        blocks[0], blocks[1], blocks[2], blocks[3] = 95, 160, 14, 14
        data[0] = 14 | (14 << 4)
        add[1] = 1 << 4  # Custom ID 270 is not vanilla gold ore.
        result = dict(w.section_blocks({'Blocks': blocks, 'Data': data, 'Add': add}, 0))
        self.assertEqual('minecraft:red_stained_glass', result[0])
        self.assertEqual('minecraft:red_stained_glass_pane', result[1])
        self.assertEqual('minecraft:gold_ore', result[2])
        self.assertEqual('', result[3])
        self.assertEqual('minecraft:cyan_terracotta', w.legacy_block(159, 9))
        with self.assertRaises(ValueError):
            list(w.section_blocks({'Blocks': b'bad'}, 0))

    def test_palette_packing_before_and_after_116_at_long_boundaries(self):
        palette = [{'Name': f'minecraft:test_{i}'} for i in range(17)]
        values = [i % 17 for i in range(4096)]
        for padded, version in ((False, 2230), (True, 2529)):
            for modern in (False, True):
                section = {'block_states': {'palette': palette, 'data': packed(values, 5, padded)}} if modern else {
                    'Palette': palette, 'BlockStates': packed(values, 5, padded)}
                decoded = list(w.section_blocks(section, version))
                self.assertEqual([f'minecraft:test_{v}' for v in values], [name for _, name in decoded])
        with self.assertRaises(ValueError):
            list(w.section_blocks({'Palette': palette, 'BlockStates': [0]}, 2230))

    def test_single_palette_negative_section_and_empty_sections(self):
        section = {'block_states': {'palette': [{'Name': 'minecraft:glass'}]}}
        self.assertEqual(4096, len(list(w.section_blocks(section, 3000))))
        self.assertEqual([], list(w.section_blocks({'Y': -4}, 3000)))

    def test_zip_region_scan_compression_external_chunks_and_cli_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            chunks = {(-1, 0): chunk(-1, 0, [15]), (0, 0): chunk(0, 0, [0])}
            # The two neighboring chunks live in different regions and their blocks share a face.
            for compression in (1, 2, 3):
                for external in (False, True):
                    source = base / 'world.zip'
                    with ZipFile(source, 'w', ZIP_DEFLATED) as zip_file:
                        zip_file.writestr('Example/level.dat', b'not needed by scanner')
                        for (cx, cz), data in chunks.items():
                            region_data, externals = region({(cx, cz): data}, compression, external)
                            zip_file.writestr(f'Example/region/r.{cx // 32}.{cz // 32}.mca', region_data)
                            for name, value in externals.items():
                                zip_file.writestr('Example/region/' + name, value)
                    scanned = w.scan_world(source, {})
                    self.assertEqual({(-1, -16, 0), (0, -16, 0)}, set(scanned))
                    self.assertEqual(1, len(w.cluster_blocks(scanned, 'test')))
                    self.assertEqual(1, len(w.scan_world(source, {}, [0, -16, 0, 0, -16, 0])))
            output = base / 'mining/nodes'
            with patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(0, w.main([f'JASP1={source}', '--island', 'MINESHAFT', '--output-dir', str(output)]))
            catalog = json.loads((output / 'JASP1.json').read_text())
            self.assertEqual('JASP1', catalog['mineshaft'])
            self.assertEqual(2, len(catalog['nodes'][0]['blocks']))
            self.assertEqual(['mining/nodes/JASP1.json'], json.loads((output.parent / 'nodes.json').read_text())['files'])
            old = (output / 'JASP1.json').read_text()
            # Invalid second input must leave the first catalog unchanged.
            with patch('sys.stdout', new=io.StringIO()), patch('sys.stderr', new=io.StringIO()):
                self.assertEqual(1, w.main([f'JASP1={source}', f'JASPC={base / "missing.zip"}', '--island', 'MINESHAFT', '--output-dir', str(output)]))
            self.assertEqual(old, (output / 'JASP1.json').read_text())
            (output.parent / 'nodes.json').write_text('{invalid index')
            with patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(0, w.main([f'JASP1={source}', '--island', 'MINESHAFT', '--output-dir', str(output), '--no-index']))
            self.assertEqual('{invalid index', (output.parent / 'nodes.json').read_text())

    def test_mineshaft_exports_only_gemstones_while_other_islands_keep_all_kinds(self):
        values = [0] * 4096
        values[0], values[1], values[2] = 1, 2, 3
        names = ['minecraft:air', 'minecraft:red_stained_glass', 'minecraft:iron_ore', 'minecraft:prismarine']
        section = {'Y': (1, 0), 'block_states': (10, {
            'palette': (9, (10, [{'Name': (8, name)} for name in names])),
            'data': (12, packed(values, 4, True)),
        })}
        raw = b'\x0a\0\0' + payload(10, {'DataVersion': (3, 3000), 'xPos': (3, 0), 'zPos': (3, 0),
                                         'sections': (9, (10, [section]))})
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'mixed.zip'
            with ZipFile(source, 'w') as zip_file:
                zip_file.writestr('Example/level.dat', b'')
                zip_file.writestr('Example/region/r.0.0.mca', region({(0, 0): raw})[0])
            rules = base / 'rules.json'
            rules.write_text(json.dumps({'minecraft:prismarine': {'kind': 'MITHRIL', 'material': 'MITHRIL'}}))
            for island, kinds in [('MINESHAFT', {'GEMSTONE'}), ('CRYSTAL_HOLLOWS', {'GEMSTONE', 'ORE', 'MITHRIL'})]:
                output = base / island / 'nodes'
                with patch('sys.stdout', new=io.StringIO()):
                    self.assertEqual(0, w.main([f'JASP1={source}', '--island', island, '--rules', str(rules),
                                               '--output-dir', str(output)]))
                nodes = json.loads((output / 'JASP1.json').read_text())['nodes']
                self.assertEqual(kinds, {node['kind'] for node in nodes})
                self.assertEqual(len(kinds), sum(len(node['blocks']) for node in nodes))
            # Irrelevant ores must not consume the gemstone survey's candidate limit.
            with patch.object(w, 'MAX_TOTAL_BLOCKS', 1):
                selected = w.scan_world(source, w.load_rules(rules), allowed_kinds={'GEMSTONE'})
                self.assertEqual(1, len(selected))

    def test_large_surveys_split_without_cutting_nodes_or_changing_ids(self):
        material = w.Material('ORE', 'IRON')
        blocks = {(i * 3 + x, 0, 0): (material, 'minecraft:iron_ore') for i in range(8) for x in (0, 1)}
        nodes = w.cluster_blocks(blocks, 'large')
        scope = {'island': 'CRYSTAL_HOLLOWS', 'space': 'WORLD'}
        small = w.survey_files('large', scope, nodes)
        self.assertEqual(['large.json'], [name for name, _ in small])
        with patch.object(w, 'MAX_FILE_BYTES', 900):
            parts = w.survey_files('large', scope, nodes)
            self.assertGreater(len(parts), 1)
            combined = []
            for name, text in parts:
                self.assertRegex(name, r'large-part-\d{3}\.json')
                self.assertLessEqual(len(text.encode('utf-8')), 900)
                catalog = json.loads(text)
                self.assertEqual('CRYSTAL_HOLLOWS', catalog['island'])
                self.assertTrue(all(len(node['blocks']) == 2 for node in catalog['nodes']))
                combined.extend(catalog['nodes'])
            self.assertEqual(json.loads(json.dumps(nodes)), combined)
        self.assertTrue(w.survey_path('mining/nodes/large.json', 'large'))
        self.assertTrue(w.survey_path('mining/nodes/large-part-001.json', 'large'))
        self.assertFalse(w.survey_path('mining/nodes/large-other.json', 'large'))
        consumed = dict(blocks)
        self.assertEqual(nodes, w.cluster_blocks(consumed, 'large', consume=True))
        self.assertEqual({}, consumed)

    def test_reexport_replaces_index_parts_and_keeps_other_surveys(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'nodes'
            index = output.parent / 'nodes.json'
            index.write_text(json.dumps({'schemaVersion': 1, 'files': [
                'mining/nodes/large-part-001.json', 'mining/nodes/large-part-002.json', 'mining/nodes/JASP1.json',
            ]}))
            material = w.Material('GEMSTONE', 'RED_GLASS')
            blocks = {(0, 0, 0): (material, 'minecraft:red_stained_glass')}
            with patch.object(w, 'scan_world', return_value=blocks), patch('sys.stdout', new=io.StringIO()):
                self.assertEqual(0, w.main(['large=unused.zip', '--island', 'CRYSTAL_HOLLOWS', '--output-dir', str(output)]))
            self.assertEqual(['mining/nodes/JASP1.json', 'mining/nodes/large.json'], json.loads(index.read_text())['files'])

    def test_corruption_and_unsupported_compression_fail_explicitly(self):
        for data, compression in ((b'abc', 2), (b'abc', 4), (zlib.compress(b'abc')[:-1], 2)):
            with self.assertRaises((ValueError, zlib.error)):
                w.decompress_chunk(data, compression)
        with self.assertRaises(ValueError):
            w.NbtReader(b'\x0a\0\0\x03').root()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'world.zip'
            with ZipFile(source, 'w') as zip_file:
                zip_file.writestr('Example/level.dat', b'')
                zip_file.writestr('Example/region/r.0.0.mca', bytes(100))
            with self.assertRaises(ValueError):
                w.scan_world(source, {})

    def test_modified_utf8_and_limit_errors(self):
        raw = b'\xc0\x80\xed\xa0\xbd\xed\xb8\x80'
        self.assertEqual('\0😀', w.NbtReader(struct.pack('>H', len(raw)) + raw).string())
        material = w.Material('ORE', 'IRON')
        with patch.object(w, 'MAX_NODE_BLOCKS', 1):
            with self.assertRaises(ValueError):
                w.cluster_blocks({(0, 0, 0): (material, 'minecraft:iron_ore'), (1, 0, 0): (material, 'minecraft:iron_ore')}, 'test')
        with patch.object(w, 'MAX_FILE_BYTES', 1):
            with self.assertRaises(ValueError):
                w.json_text({'nodes': []})


if __name__ == '__main__':
    unittest.main()
