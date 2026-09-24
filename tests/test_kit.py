from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / 'payload/.codex/es'))
sys.path.insert(0, str(KIT))
from evidence import (EvidenceError, MAX_HANDOFF_BYTES, load_handoff, read_range,
                      verify_handoff, validate_shape, source_path)
from install import install


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'src').mkdir()
        self.raw = b'def route(x):\n    return normalize(x)\n\ndef normalize(x):\n    return x.strip()\n'
        (self.root / 'src/router.py').write_bytes(self.raw)
        self.site = {'path':'src/router.py','start':1,'end':2,
                     'sha256':hashlib.sha256(self.raw).hexdigest(), 'symbol':'route',
                     'evidence':'Calls normalize before returning.'}
        self.data = {'version':1,'status':'ready','stop_reason':'evidence_ready',
                     'primary':[self.site], 'related':[], 'unresolved':[]}

    def tearDown(self):
        self.temp.cleanup()

    def test_valid(self):
        result = verify_handoff(self.root, self.data)
        self.assertTrue(result['ok'])
        self.assertFalse(result['semantic_relevance_verified'])

    def test_read_exact(self):
        result = read_range(self.root, 'src/router.py', 1, 2)
        self.assertEqual(result['sha256'], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(result['source'], '1: def route(x):\n2:     return normalize(x)')

    def test_stale_same_head(self):
        (self.root / 'src/router.py').write_bytes(self.raw + b'# uncommitted change\n')
        with self.assertRaisesRegex(EvidenceError, 'stale_source'):
            verify_handoff(self.root, self.data)

    def test_stale_after_inserted_lines(self):
        (self.root / 'src/router.py').write_bytes(b'# inserted\n' + self.raw)
        with self.assertRaisesRegex(EvidenceError, 'stale_source'):
            read_range(self.root, 'src/router.py', 1, 2, self.site['sha256'])

    def test_range_beyond_end(self):
        with self.assertRaises(EvidenceError):
            read_range(self.root, 'src/router.py', 1, 20)

    def test_boolean_line_number(self):
        self.site['start'] = True
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_zero_line_number(self):
        self.site['start'] = 0
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_reversed_range(self):
        self.site['start'], self.site['end'] = 2, 1
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_too_long_range(self):
        self.site['end'] = 161
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_total_line_budget(self):
        self.data['primary'] = [dict(self.site, path=f'src/{i}.py', start=1, end=160) for i in range(3)]
        self.data['related'] = [dict(self.site, path='src/four.py', start=1, end=1)]
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_too_many_primaries(self):
        self.data['primary'] = [dict(self.site, path=f'src/{i}.py') for i in range(4)]
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_duplicate_location(self):
        self.data['related'] = [dict(self.site)]
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_overlapping_locations(self):
        self.data['related'] = [dict(self.site, start=2, end=3)]
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_unknown_fields(self):
        self.site['patch'] = 'not permitted'
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_empty_ready_is_invalid(self):
        self.data['primary'] = []
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_partial_and_missing_test(self):
        self.data.update(status='partial', stop_reason='budget', unresolved=['Registration target not traced.'])
        self.assertTrue(verify_handoff(self.root, self.data)['ok'])

    def test_not_found_is_valid_without_fabricated_reference(self):
        self.data.update(status='not_found',stop_reason='no_match',primary=[],unresolved=['No error string in inspected src directory.'])
        self.assertTrue(verify_handoff(self.root, self.data)['ok'])

    def test_blocked_is_explicit(self):
        self.data.update(status='blocked',stop_reason='environment',primary=[],unresolved=['Source directory is unreadable.'])
        self.assertTrue(verify_handoff(self.root, self.data)['ok'])

    def test_digest_not_invented_by_validator(self):
        self.site['sha256'] = '0'*64
        with self.assertRaisesRegex(EvidenceError, 'stale_source'):
            verify_handoff(self.root, self.data)

    def test_path_escape(self):
        for path in ['../outside', '/etc/passwd', 'src/../router.py', 'C:\\repo\\file.py', 'src//router.py']:
            with self.subTest(path=path), self.assertRaises(EvidenceError):
                source_path(self.root, path)

    def test_symlink_escape(self):
        with tempfile.TemporaryDirectory() as outside:
            dest = Path(outside)/'private.py'
            dest.write_text('SECRET')
            (self.root/'src/link.py').symlink_to(dest)
            with self.assertRaises(EvidenceError):
                read_range(self.root,'src/link.py',1,1)

    def test_credentials_excluded(self):
        (self.root/'.env').write_text('KEY=private\n')
        with self.assertRaises(EvidenceError):
            read_range(self.root,'.env',1,1)

    def test_binary_excluded(self):
        (self.root/'src/binary.py').write_bytes(b'\x00\x01')
        with self.assertRaises(EvidenceError):
            read_range(self.root,'src/binary.py',1,1)

    def test_invalid_utf8_excluded(self):
        (self.root/'src/binary.py').write_bytes(b'\xff')
        with self.assertRaises(EvidenceError):
            read_range(self.root,'src/binary.py',1,1)

    def test_crlf_and_unicode_preserve_digest(self):
        raw = '名前 = "値"\r\n次 = 1\r\n'.encode()
        (self.root/'src/jp.py').write_bytes(raw)
        result = read_range(self.root,'src/jp.py',1,2)
        self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['source'], '1: 名前 = "値"\n2: 次 = 1')

    def test_unicode_line_separator_is_not_newline(self):
        (self.root/'src/line.py').write_text('x = "a\u2028b"\n')
        with self.assertRaises(EvidenceError):
            read_range(self.root,'src/line.py',1,2)

    def test_long_line_never_silently_truncated(self):
        (self.root/'src/long.py').write_text('x'*17000+'\n')
        with self.assertRaisesRegex(EvidenceError,'narrow'):
            read_range(self.root,'src/long.py',1,1)

    def test_oversize_handoff(self):
        with self.assertRaises(EvidenceError):
            load_handoff(b' '* (MAX_HANDOFF_BYTES+1))

    def test_duplicate_json_key(self):
        with self.assertRaisesRegex(EvidenceError, 'duplicate JSON key'):
            load_handoff(b'{"version":1,"version":1}')

    def test_markdown_json_not_accepted(self):
        with self.assertRaises(EvidenceError):
            load_handoff(('```json\n'+json.dumps(self.data)+'\n```').encode())

    def test_normal_json_roundtrip(self):
        self.assertEqual(load_handoff(json.dumps(self.data).encode()), self.data)


class ConfigInstallerTests(unittest.TestCase):
    def test_agy_agents_are_main_only_and_parent_disabled(self):
        import agy_backend
        self.assertFalse((KIT/'payload/.codex/agents').exists())
        cfg=tomllib.loads((KIT/'payload/.codex/es/config.snippet.toml').read_text())
        self.assertFalse(cfg['agents']['enabled'])
        for name in ('es-explorer','es-deep-explorer','es-reader'):
            tools=[] if name=='es-reader' else ['view_file','grep_search']
            agy_backend.agent_definition(KIT/'payload/.codex/es/agy_agents'/f'{name}.md',name,tools)

    def test_skill_is_explicit_opt_in(self):
        text=(KIT/'payload/.agents/skills/explore-solve/agents/openai.yaml').read_text()
        self.assertIn('allow_implicit_invocation: false',text)

    def test_create_only_install_preserves_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'.git').mkdir(); (root/'.codex').mkdir()
            config=root/'.codex/config.toml'; config.write_text('[agents]\nenabled=false\n')
            agents=root/'AGENTS.md'; agents.write_text('Existing project instructions.\n')
            install(root,apply=False,model='gemini-3.8-flash-medium',deep_model='gemini-3.8-flash-high')
            self.assertFalse((root/'.codex/es').exists())
            files=install(root,apply=True,model='gemini-3.8-flash-medium',deep_model='gemini-3.8-flash-high')
            self.assertGreater(len(files),5)
            self.assertEqual(config.read_text(),'[agents]\nenabled=false\n')
            self.assertEqual(agents.read_text(),'Existing project instructions.\n')
            role=tomllib.loads((root/'.codex/es/agy.toml').read_text())
            self.assertEqual(role['explorer_model'],'gemini-3.8-flash-medium')
            with self.assertRaises(ValueError):
                install(root,apply=True,model='gemini-3.8-flash-high',deep_model='gemini-3.8-flash-high')
            self.assertEqual(tomllib.loads((root/'.codex/es/agy.toml').read_text())['explorer_model'],'gemini-3.8-flash-medium')

    def test_install_symlink_destination_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root=Path(tmp); (root/'.git').mkdir(); (root/'.codex').symlink_to(outside)
            with self.assertRaises(ValueError):
                install(root,apply=True,model='gemini-3.8-flash-medium',deep_model='gemini-3.8-flash-high')
            self.assertEqual(list(Path(outside).iterdir()),[])


if __name__=='__main__': unittest.main()
