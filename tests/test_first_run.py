"""First-run preparation checks with isolated files and no installs or servers."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing, nullcontext
from unittest.mock import patch

from scripts import first_run


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='campus-first-run-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'extracted-project'
        self.root.mkdir()
        (self.root / 'scripts').mkdir()
        (self.root / 'run.py').write_text('# fixture\n', encoding='utf-8')
        (self.root / 'scripts' / 'bootstrap_knowledge.py').write_text('# fixture\n', encoding='utf-8')
        (self.root / 'materials').mkdir()
        (self.root / 'materials' / 'manifest.json').write_text('[]', encoding='utf-8')
        (self.root / 'requirements.txt').write_text('fastapi==0.141.1\n', encoding='utf-8')
        self.python = self.root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.data = self.root / 'data'
        self.marker = self.data / '.first-run.json'

    def create_database(self, sample):
        self.data.mkdir(exist_ok=True)
        with closing(sqlite3.connect(self.data / 'knowledge.db')) as database, database:
            database.execute('CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, sample INTEGER NOT NULL)')
            identifier = 'sample' if sample else 'formal'
            database.execute('INSERT OR IGNORE INTO documents VALUES (?,?)', (identifier, int(sample)))

    def mark(self, status):
        self.data.mkdir(exist_ok=True)
        self.marker.write_text(json.dumps({'status': status}), encoding='utf-8')

    def successful_bootstrap(self, command, **kwargs):
        self.assertTrue(any(Path(str(part)).name == 'bootstrap_knowledge.py' for part in command))
        self.assertIn('--data-dir', command)
        position = command.index('--data-dir')
        self.assertEqual(Path(command[position + 1]), self.data)
        self.create_database(sample=False)
        return subprocess.CompletedProcess(command, 0)

    def create_environment(self):
        self.python.parent.mkdir(parents=True)
        self.python.touch()

    def test_fresh_and_sample_only_libraries_get_bundled_materials(self):
        for existing_samples in (False, True):
            with self.subTest(existing_samples=existing_samples):
                if existing_samples:
                    self.create_database(sample=True)
                    self.marker.unlink(missing_ok=True)
                    with closing(sqlite3.connect(self.data / 'knowledge.db')) as database, database:
                        database.execute('DELETE FROM documents WHERE sample=0')
                with patch.object(first_run.subprocess, 'run', side_effect=self.successful_bootstrap) as run:
                    first_run.ensure_corpus(self.root, self.python, self.data)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(json.loads(self.marker.read_text(encoding='utf-8'))['status'], 'complete')
                with closing(sqlite3.connect(self.data / 'knowledge.db')) as database:
                    self.assertGreater(database.execute('SELECT COUNT(*) FROM documents WHERE sample=0').fetchone()[0], 0)

    def test_legacy_formal_library_settings_and_encrypted_credential_are_preserved(self):
        self.create_database(sample=False)
        sentinels = {
            self.data / 'settings.json': b'{"enabled":true,"model":"user-selected"}',
            self.data / 'credential.dpapi': b'fixture-encrypted-user-credential',
            self.data / 'uploads' / 'user-document.txt': b'user-owned-document',
        }
        for path, content in sentinels.items():
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(content)
        database_before = (self.data / 'knowledge.db').read_bytes()
        with patch.object(first_run.subprocess, 'run') as run:
            first_run.ensure_corpus(self.root, self.python, self.data)
        run.assert_not_called()
        self.assertEqual((self.data / 'knowledge.db').read_bytes(), database_before)
        for path, content in sentinels.items():
            self.assertEqual(path.read_bytes(), content)

    def test_pending_partial_import_is_resumed_even_when_formal_documents_exist(self):
        self.create_database(sample=False)
        self.mark('pending')
        with patch.object(first_run.subprocess, 'run', side_effect=self.successful_bootstrap) as run:
            first_run.ensure_corpus(self.root, self.python, self.data)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(self.marker.read_text(encoding='utf-8'))['status'], 'complete')

    def test_failed_import_stays_pending_and_the_next_attempt_can_complete(self):
        with patch.object(first_run.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7)):
            with self.assertRaises(RuntimeError):
                first_run.ensure_corpus(self.root, self.python, self.data)
        self.assertEqual(json.loads(self.marker.read_text(encoding='utf-8'))['status'], 'pending')
        self.create_database(sample=False)
        with patch.object(first_run.subprocess, 'run', side_effect=self.successful_bootstrap):
            first_run.ensure_corpus(self.root, self.python, self.data)
        self.assertEqual(json.loads(self.marker.read_text(encoding='utf-8'))['status'], 'complete')

    def test_completed_import_does_not_restore_documents_the_user_deleted(self):
        self.create_database(sample=True)
        self.mark('complete')
        with patch.object(first_run.subprocess, 'run') as run:
            first_run.ensure_corpus(self.root, self.python, self.data)
        run.assert_not_called()
        with closing(sqlite3.connect(self.data / 'knowledge.db')) as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM documents WHERE sample=0').fetchone()[0], 0)

    def test_unreadable_existing_library_is_not_overwritten_or_imported(self):
        self.data.mkdir()
        database = self.data / 'knowledge.db'
        original = b'fixture: not a readable SQLite database'
        database.write_bytes(original)
        with patch.object(first_run.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                first_run.ensure_corpus(self.root, self.python, self.data)
        run.assert_not_called()
        self.assertEqual(database.read_bytes(), original)
        self.assertFalse(self.marker.exists())

    def test_usable_existing_environment_is_adopted_without_installing_packages(self):
        self.create_environment()
        with patch.object(first_run.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(first_run.ensure_environment(self.root), self.python)
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all('pip' not in call.args[0] for call in run.call_args_list))
        marker = self.root / '.venv' / '.campus-requirements.json'
        expected = hashlib.sha256((self.root / 'requirements.txt').read_bytes()).hexdigest()
        self.assertEqual(json.loads(marker.read_text(encoding='utf-8'))['sha256'], expected)

    def test_changed_requirements_are_installed_before_the_marker_is_updated(self):
        self.create_environment()
        marker = self.root / '.venv' / '.campus-requirements.json'
        marker.write_text(json.dumps({'sha256': 'older-requirements'}), encoding='utf-8')

        def successful_command(command, **kwargs):
            if 'install' in command:
                self.assertEqual(json.loads(marker.read_text(encoding='utf-8'))['sha256'], 'older-requirements')
            return subprocess.CompletedProcess(command, 0)

        with patch.object(first_run.subprocess, 'run', side_effect=successful_command) as run:
            first_run.ensure_environment(self.root)
        self.assertTrue(any('pip' in call.args[0] and 'install' in call.args[0] for call in run.call_args_list))
        expected = hashlib.sha256((self.root / 'requirements.txt').read_bytes()).hexdigest()
        self.assertEqual(json.loads(marker.read_text(encoding='utf-8'))['sha256'], expected)

    def test_dependency_install_failure_never_marks_success_imports_materials_or_starts_app(self):
        self.create_environment()
        self.data.mkdir()
        settings = self.data / 'settings.json'
        original = b'{"model":"keep-user-setting"}'
        settings.write_bytes(original)

        def failing_install(command, **kwargs):
            if '-c' in command:
                if 'import sys, struct, venv, ensurepip' in command[command.index('-c') + 1]:
                    return subprocess.CompletedProcess(command, 0)
                return subprocess.CompletedProcess(command, 1, stderr=b'fixture: missing package')
            if 'pip' in command and '--version' in command:
                return subprocess.CompletedProcess(command, 0)
            if 'pip' in command and 'install' in command:
                return subprocess.CompletedProcess(command, 7)
            self.fail('Unexpected command after dependency failure: ' + repr(command))

        with patch.object(first_run, 'ROOT', self.root), \
                patch.dict(os.environ, {'CAMPUS_DATA_DIR': str(self.data)}), \
                patch.object(first_run, 'prepare_lock', return_value=nullcontext(True)), \
                patch.object(first_run.subprocess, 'run', side_effect=failing_install), \
                patch.object(first_run, 'ensure_corpus') as corpus, patch('builtins.print'):
            self.assertEqual(first_run.main([]), 1)
        corpus.assert_not_called()
        self.assertFalse((self.root / '.venv' / '.campus-requirements.json').exists())
        self.assertFalse(self.marker.exists())
        self.assertEqual(settings.read_bytes(), original)

    def check_invalid_environment_is_preserved_and_rebuilt(self, cannot_execute):
        self.create_environment()
        sentinel = self.root / '.venv' / 'keep-old-environment.txt'
        sentinel.write_bytes(b'fixture: preserve instead of deleting')
        self.data.mkdir()
        settings = self.data / 'settings.json'
        settings.write_bytes(b'{"model":"preserve-user-config"}')
        installed = False

        def rebuild(command, **kwargs):
            nonlocal installed
            if '-c' in command:
                check = command[command.index('-c') + 1]
                if 'import sys, struct, venv, ensurepip' in check:
                    if cannot_execute:
                        raise OSError('fixture: broken environment executable')
                    return subprocess.CompletedProcess(command, 1)
                return subprocess.CompletedProcess(command, 0 if installed else 1, stderr=b'fixture: missing package')
            if '-m' in command and command[command.index('-m') + 1] == 'venv':
                self.assertFalse(sentinel.exists())
                preserved = list((self.root / '.tools').glob('venv-incomplete-*/keep-old-environment.txt'))
                self.assertEqual(len(preserved), 1)
                self.assertEqual(preserved[0].read_bytes(), b'fixture: preserve instead of deleting')
                self.python.parent.mkdir(parents=True)
                self.python.touch()
                return subprocess.CompletedProcess(command, 0)
            if 'pip' in command and '--version' in command:
                return subprocess.CompletedProcess(command, 0)
            if 'pip' in command and 'install' in command:
                self.assertTrue(self.python.is_file())
                installed = True
                return subprocess.CompletedProcess(command, 0)
            self.fail('Unexpected recovery command: ' + repr(command))

        with patch.object(first_run.subprocess, 'run', side_effect=rebuild), patch('builtins.print'):
            self.assertEqual(first_run.ensure_environment(self.root), self.python)
        self.assertTrue(installed)
        self.assertTrue(self.python.is_file())
        self.assertFalse(sentinel.exists())
        preserved = list((self.root / '.tools').glob('venv-incomplete-*/keep-old-environment.txt'))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_bytes(), b'fixture: preserve instead of deleting')
        self.assertEqual(settings.read_bytes(), b'{"model":"preserve-user-config"}')
        self.assertTrue((self.root / '.venv' / '.campus-requirements.json').is_file())

    def test_wrong_version_environment_is_preserved_and_rebuilt(self):
        self.check_invalid_environment_is_preserved_and_rebuilt(cannot_execute=False)

    def test_unexecutable_environment_is_preserved_and_rebuilt(self):
        self.check_invalid_environment_is_preserved_and_rebuilt(cannot_execute=True)

    def test_prepare_honors_custom_data_directory(self):
        custom_data = self.root / 'user-selected-data'
        with patch.dict(os.environ, {'CAMPUS_DATA_DIR': str(custom_data)}), \
                patch.object(first_run, 'prepare_lock', return_value=nullcontext(True)), \
                patch.object(first_run, 'ensure_environment', return_value=self.python) as environment, \
                patch.object(first_run, 'ensure_corpus') as corpus:
            self.assertEqual(first_run.prepare(self.root), self.python)
        environment.assert_called_once_with(self.root)
        corpus.assert_called_once_with(self.root, self.python, custom_data)
        self.assertFalse((self.root / 'data').exists())

    def test_busy_preparation_does_not_install_or_import_in_parallel(self):
        with patch.object(first_run, 'prepare_lock', return_value=nullcontext(False)), \
                patch.object(first_run, 'ensure_environment') as environment, \
                patch.object(first_run, 'ensure_corpus') as corpus, patch('builtins.print'):
            self.assertIsNone(first_run.prepare(self.root))
        environment.assert_not_called()
        corpus.assert_not_called()

    def test_preparation_failure_stops_before_the_server_is_started(self):
        with patch.object(first_run, 'ROOT', self.root), \
                patch.object(first_run, 'prepare', side_effect=RuntimeError('fixture: dependency installation failed')), \
                patch.object(first_run.subprocess, 'run') as run, patch('builtins.print'):
            self.assertEqual(first_run.main([]), 1)
        run.assert_not_called()

    def test_prepare_only_does_not_launch_the_server(self):
        with patch.object(first_run, 'ROOT', self.root), \
                patch.object(first_run, 'prepare', return_value=self.python), \
                patch.object(first_run.subprocess, 'run') as run:
            self.assertEqual(first_run.main(['--prepare-only']), 0)
        run.assert_not_called()

    def test_server_options_and_exit_status_are_forwarded(self):
        with patch.object(first_run, 'ROOT', self.root), \
                patch.object(first_run, 'prepare', return_value=self.python), \
                patch.object(first_run.subprocess, 'run', return_value=subprocess.CompletedProcess([], 3)) as run:
            self.assertEqual(first_run.main(['--no-browser', '--port', '8766']), 3)
        command = run.call_args.args[0]
        self.assertEqual([str(part) for part in command], [str(self.python), str(self.root / 'run.py'), '--no-browser', '--port', '8766'])
        self.assertEqual(Path(run.call_args.kwargs['cwd']), self.root)


if __name__ == '__main__':
    unittest.main()
