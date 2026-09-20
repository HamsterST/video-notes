import importlib.util
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).resolve().parents[1] / 'scripts/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'project with spaces'
        self.root.mkdir()
        for folder in ('video_notes', 'extension', 'rules', 'scripts'):
            (self.root / folder).mkdir()
        for filename in ('SKILL.md', 'pyproject.toml', 'rules/notes-generation.md'):
            (self.root / filename).write_text('fixture', encoding='utf-8')
        import shutil
        shutil.copy2(Path(__file__).resolve().parents[1] / 'video_notes/paths.py', self.root / 'video_notes/paths.py')
        (self.root / 'storage.json').write_text(json.dumps({'data_dir': str(self.root / 'data')}))
        patcher = patch.object(bootstrap.sys, 'platform', 'win32')
        patcher.start()
        self.addCleanup(patcher.stop)
        probe = patch.object(bootstrap, "dependencies_available", return_value=False)
        self.probe = probe.start()
        self.addCleanup(probe.stop)

    def fake_run(self, args, **kwargs):
        if 'venv' in args:
            python = self.root / '.venv/Scripts/python.exe'
            python.parent.mkdir(parents=True)
            python.write_bytes(b'fixture')
        if '--set-model' in args:
            (self.root / 'data').mkdir(exist_ok=True)
            (self.root / 'data/settings.json').write_text(json.dumps({'model': args[-1]}), encoding='utf-8')

    def test_single_choice_no_script_questions_and_paths_inside_install(self):
        with patch.object(bootstrap.subprocess, 'run', side_effect=self.fake_run) as run, patch('builtins.input', side_effect=AssertionError('extra question')):
            result = bootstrap.install(self.root, 'large-v3-turbo')
        self.assertEqual(result['status'], 'runtime_ready')
        self.assertEqual(result['browser_extension'], 'manual_load_required')
        runtime = json.loads((self.root / 'runtime.json').read_text(encoding='utf-8'))
        self.assertEqual(runtime['python'], str(self.root / '.venv/Scripts/python.exe'))
        for call in run.call_args_list:
            self.assertIsInstance(call.args[0], list)
            self.assertEqual(call.kwargs['cwd'], self.root)
            for key in ('TEMP', 'TMP'):
                self.assertFalse(Path(call.kwargs['env'][key]).exists())
            self.assertEqual(call.kwargs['env']['PIP_NO_CACHE_DIR'], '1')
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any('--set-model' in c and c[-1] == 'large-v3-turbo' for c in commands))
        self.assertFalse(any('download_model' in str(c) for c in commands))

    def test_rerun_preserves_choice_and_reuses_venv(self):
        with patch.object(bootstrap.subprocess, 'run', side_effect=self.fake_run):
            bootstrap.install(self.root, 'small')
        with patch.object(bootstrap.subprocess, 'run', side_effect=self.fake_run) as run:
            result = bootstrap.install(self.root)
        self.assertFalse(any('venv' in call.args[0] for call in run.call_args_list))

    def test_first_install_needs_no_model_or_transcription_dependency(self):
        with patch.object(bootstrap.subprocess, 'run', side_effect=self.fake_run) as run, patch('builtins.input', side_effect=AssertionError('question')):
            result = bootstrap.install(self.root)
        self.assertEqual(result['transcription'], 'on_demand')
        commands = [c.args[0] for c in run.call_args_list]
        self.assertFalse(any('faster_whisper' in str(c) or '.[transcribe]' in c or '--set-model' in c for c in commands))
        self.assertFalse((self.root / 'data/settings.json').exists())

    def test_explicit_transcription_install_requires_choice(self):
        with patch.object(bootstrap.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, '模型'):
                bootstrap.install(self.root, transcribe=True)
            run.assert_not_called()
        with patch.object(bootstrap.subprocess, 'run', side_effect=self.fake_run) as run:
            result = bootstrap.install(self.root, 'small', transcribe=True)
        self.assertEqual(result['transcription'], 'ready')
        self.assertTrue(any('faster_whisper' in str(c) for c in run.call_args_list))

    def test_dependency_failure_does_not_claim_runtime_ready(self):
        with patch.object(bootstrap.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['pip'])):
            with self.assertRaises(subprocess.CalledProcessError):
                bootstrap.install(self.root, 'small')
        self.assertFalse((self.root / 'runtime.json').exists())

    def test_compatible_existing_python_is_never_modified(self):
        self.probe.return_value = True
        with patch.object(bootstrap.subprocess, 'run', side_effect=self.fake_run) as run:
            result = bootstrap.install(self.root)
        self.assertEqual(result['environment'], 'reused')
        self.assertEqual(result['runtime']['python'], str(Path(bootstrap.sys.executable).resolve()))
        self.assertFalse((self.root / '.venv').exists())
        self.assertFalse(any('pip' in c.args[0] or 'venv' in c.args[0] for c in run.call_args_list))

    def test_missing_speech_dependencies_use_venv_without_modifying_shared_python(self):
        self.probe.side_effect = lambda python, root, env, transcribe: not transcribe
        with patch.object(bootstrap.subprocess, 'run', side_effect=self.fake_run) as run:
            first = bootstrap.install(self.root)
            second = bootstrap.install(self.root, 'small', transcribe=True)
        self.assertEqual(first['environment'], 'reused')
        self.assertEqual(second['environment'], 'managed_venv')
        pip_calls = [c.args[0] for c in run.call_args_list if 'pip' in c.args[0]]
        self.assertTrue(pip_calls)
        self.assertTrue(all(c[0] == str(self.root / '.venv/Scripts/python.exe') for c in pip_calls))

    def test_compatible_recorded_environment_takes_priority(self):
        recorded = self.root / 'existing-python.exe'; recorded.write_bytes(b'fixture')
        (self.root / 'runtime.json').write_text(json.dumps({'python': str(recorded)}))
        self.probe.return_value = True
        with patch.object(bootstrap.subprocess, 'run', side_effect=self.fake_run):
            result = bootstrap.install(self.root)
        self.assertEqual(result['runtime']['python'], str(recorded))
