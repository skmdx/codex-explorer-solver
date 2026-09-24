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
from evidence import (EvidenceError, load_handoff, read_range,
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
                     'sha256':hashlib.sha256(self.raw).hexdigest(), 'encoding':'utf-8', 'symbol':'route',
                     'evidence':'Calls normalize before returning.'}
        self.data = {'version':3,'status':'ready',
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

    def show(self):
        return subprocess.run([sys.executable,str(KIT/'payload/.codex/es/evidence.py'),
            'show','--root',str(self.root),'--handoff','-'],
            input=json.dumps(self.data),text=True,capture_output=True)

    def test_show_returns_verified_originals_and_unresolved(self):
        self.data.update(status='partial',unresolved=['Find caller'])
        self.data['related']=[dict(self.site,start=4,end=5,symbol='normalize')]
        r=self.show();self.assertEqual(r.returncode,0,r.stderr)
        report=json.loads(r.stdout)
        self.assertEqual(report['primary'][0]['source'],'1: def route(x):\n2:     return normalize(x)')
        self.assertEqual(report['related'][0]['source'],'4: def normalize(x):\n5:     return x.strip()')
        self.assertEqual(report['unresolved'],['Find caller'])
        self.assertFalse(report['semantic_relevance_verified'])

    def test_show_emits_no_source_if_later_reference_is_stale(self):
        (self.root/'src/other.py').write_bytes(self.raw+b'# changed\n')
        self.data['related']=[dict(self.site,path='src/other.py')]
        r=self.show();self.assertEqual(r.returncode,2)
        self.assertEqual(r.stdout,'');self.assertIn('stale_source',r.stderr)

    def test_show_returns_all_verified_ranges_without_combined_size_refusal(self):
        raw=('x'*9000+'\n'+'y'*9000+'\n').encode()
        (self.root/'src/router.py').write_bytes(raw)
        self.site.update(start=1,end=1,sha256=hashlib.sha256(raw).hexdigest())
        self.data['related']=[dict(self.site,start=2,end=2)]
        self.assertTrue(verify_handoff(self.root,self.data)['ok'])
        r=self.show();self.assertEqual(r.returncode,0,r.stderr)
        report=json.loads(r.stdout)
        self.assertEqual(report['primary'][0]['source'],'1: '+'x'*9000)
        self.assertEqual(report['related'][0]['source'],'2: '+'y'*9000)

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

    def test_long_source_range_is_returned(self):
        raw=b'line\n'*200
        (self.root/'src/router.py').write_bytes(raw)
        self.site.update(end=200,sha256=hashlib.sha256(raw).hexdigest())
        result=verify_handoff(self.root,self.data,include_source=True)
        self.assertEqual(len(result['primary'][0]['source'].splitlines()),200)

    def test_total_lines_do_not_refuse_valid_report(self):
        self.data['primary'] = [dict(self.site, path=f'src/{i}.py', start=1, end=160) for i in range(3)]
        self.data['related'] = [dict(self.site, path='src/four.py', start=1, end=1)]
        validate_shape(self.data)

    def test_cross_file_handoff_returns_all_evidence_and_open_questions(self):
        for i in range(8):
            (self.root / f'src/{i}.py').write_bytes(self.raw)
        refs = [dict(self.site, path=f'src/{i}.py') for i in range(8)]
        self.data.update(status='partial', primary=refs[:4], related=refs[4:],
                         unresolved=[f'Check caller {i}' for i in range(5)])
        r = self.show()
        self.assertEqual(r.returncode, 0, r.stderr)
        report = json.loads(r.stdout)
        self.assertEqual(len(report['primary']) + len(report['related']), 8)
        self.assertEqual(report['unresolved'], self.data['unresolved'])
        self.assertTrue(all(ref['source'] == '1: def route(x):\n2:     return normalize(x)'
                            for group in ('primary', 'related') for ref in report[group]))

    def test_same_source_can_support_multiple_findings(self):
        self.data['related'] = [dict(self.site)]
        self.data['related'][0]['evidence'] = 'A second observation about the same function.'
        report = verify_handoff(self.root, self.data, include_source=True)
        self.assertEqual(report['primary'][0]['source'], report['related'][0]['source'])

    def test_overlapping_locations(self):
        self.data['related'] = [dict(self.site, start=2, end=3)]
        report = verify_handoff(self.root, self.data, include_source=True)
        self.assertEqual(report['related'][0]['source'], '2:     return normalize(x)\n3: ')

    def test_long_multiline_explanation_and_symbol(self):
        self.site.update(symbol='qualified_symbol_'*30, evidence='observed fact\n'*80)
        self.data.update(status='partial', unresolved=['remaining question\n'*80])
        self.assertTrue(verify_handoff(self.root, self.data)['ok'])

    def test_long_posix_source_path(self):
        relative = '/'.join(['nested_source_directory']*20 + ['name:with\\backslash.py'])
        p = self.root / relative
        p.parent.mkdir(parents=True)
        p.write_bytes(self.raw)
        self.site['path'] = relative
        self.assertEqual(verify_handoff(self.root, self.data, include_source=True)['primary'][0]['source'],
                         '1: def route(x):\n2:     return normalize(x)')

    def test_unknown_fields(self):
        self.site['patch'] = 'not permitted'
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_empty_ready_is_invalid(self):
        self.data['primary'] = []
        with self.assertRaises(EvidenceError):
            validate_shape(self.data)

    def test_partial_and_missing_test(self):
        self.data.update(status='partial',  unresolved=['Registration target not traced.'])
        self.assertTrue(verify_handoff(self.root, self.data)['ok'])

    def test_not_found_is_valid_without_fabricated_reference(self):
        self.data.update(status='not_found',primary=[],unresolved=['No error string in inspected src directory.'])
        self.assertTrue(verify_handoff(self.root, self.data)['ok'])

    def test_blocked_is_explicit(self):
        self.data.update(status='blocked',primary=[],unresolved=['Source directory is unreadable.'])
        self.assertTrue(verify_handoff(self.root, self.data)['ok'])

    def test_digest_not_invented_by_validator(self):
        self.site['sha256'] = '0'*64
        with self.assertRaisesRegex(EvidenceError, 'stale_source'):
            verify_handoff(self.root, self.data)

    def test_explicit_paths_accept_absolute_and_relative_spelling(self):
        expected=self.root/'src/router.py'
        for path in [str(expected),'./src/router.py','src//router.py','src/../src/router.py']:
            self.assertEqual(source_path(self.root,path),expected)

    def test_explicit_symlink_to_external_source(self):
        with tempfile.TemporaryDirectory() as outside:
            dest = Path(outside)/'private.py'
            dest.write_text('SECRET')
            (self.root/'src/link.py').symlink_to(dest)
            self.assertEqual(read_range(self.root,'src/link.py',1,1)['source'],'1: SECRET')

    def test_explicit_config_file_is_read(self):
        (self.root/'.env').write_text('KEY=private\n')
        self.assertEqual(read_range(self.root,'.env',1,1)['source'],'1: KEY=private')

    def test_binary_excluded(self):
        (self.root/'src/binary.py').write_bytes(b'\x00\x01')
        with self.assertRaises(EvidenceError):
            read_range(self.root,'src/binary.py',1,1)

    def test_known_detection_error_can_be_corrected(self):
        (self.root/'src/jp.py').write_bytes('日本語'.encode('cp932'))
        self.assertEqual(read_range(self.root,'src/jp.py',1,1,encoding='cp932')['source'],'1: 日本語')

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
        self.assertEqual(read_range(self.root,'src/long.py',1,1)['source'],'1: '+'x'*17000)

    def test_multibyte_report_within_schema_limits(self):
        refs=[dict(self.site,path=f'src/{n}.py',symbol='関'*160,evidence='漢'*220) for n in range(5)]
        self.data.update(primary=refs[:3],related=refs[3:])
        raw=json.dumps(self.data,ensure_ascii=False).encode()
        self.assertGreater(len(raw),6144)
        self.assertEqual(load_handoff(raw),self.data)

    def test_old_protocol_is_not_silently_reinterpreted(self):
        self.data['version']=1
        with self.assertRaisesRegex(EvidenceError,'version'):
            validate_shape(self.data)

    def test_duplicate_json_key(self):
        with self.assertRaisesRegex(EvidenceError, 'duplicate JSON key'):
            load_handoff(b'{"version":1,"version":1}')

    def test_markdown_json_not_accepted(self):
        with self.assertRaises(EvidenceError):
            load_handoff(('```json\n'+json.dumps(self.data)+'\n```').encode())

    def test_normal_json_roundtrip(self):
        self.assertEqual(load_handoff(json.dumps(self.data).encode()), self.data)


class ConfigInstallerTests(unittest.TestCase):
    def test_explicit_provider_models_are_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'.git').mkdir()
            install(root, apply=True, model='gemini-3.7-flash-medium', deep_model='gemini-3.1-pro-high')
            config = tomllib.loads((root/'.codex/es/agy.toml').read_text())
            self.assertEqual(config['reader_model'], 'gemini-3.7-flash-medium')
            self.assertEqual(config['deep_model'], 'gemini-3.1-pro-high')
    def test_parent_delegation_is_disabled(self):
        self.assertFalse((KIT/'payload/.codex/agents').exists())
        cfg=tomllib.loads((KIT/'payload/.codex/es/config.snippet.toml').read_text())
        self.assertFalse(cfg['agents']['enabled'])

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
