import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from video_notes import cache, paths, storage, settings
from video_notes.media import download_and_transcribe
from video_notes.cli import main

class LazyStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name, value in [('video_notes.cache.CACHE_DIR', self.root/'cache'),
                            ('video_notes.settings.SETTINGS_FILE', self.root/'settings.json'),
                            ('video_notes.settings.MODELS_DIR', self.root/'models')]:
            p = patch(name, value); p.start(); self.addCleanup(p.stop)

    def engine(self):
        model = Mock()
        model.return_value.transcribe.return_value = (iter([SimpleNamespace(start=0,end=5,text='hi')]), SimpleNamespace(duration=5,language='en'))
        return SimpleNamespace(WhisperModel=model)

    def test_missing_model_and_dependency_do_not_download(self):
        with patch('sys.stdin.isatty', return_value=False), patch('video_notes.media.yt_dlp.YoutubeDL') as net:
            result = download_and_transcribe('https://example.org/v', download_dir=self.root/'downloads')
            self.assertEqual(result['status'], 'model_selection_required')
            self.assertFalse(settings.SETTINGS_FILE.exists())
            with patch.dict('sys.modules', {'faster_whisper': None}):
                result = download_and_transcribe('https://example.org/v', model='small', download_dir=self.root/'downloads')
            self.assertEqual(result['status'], 'dependency_missing')
            net.assert_not_called()

    def test_subtitle_cli_never_asks_model_or_imports_whisper(self):
        f=self.root/'a.srt'; f.write_text('1\n00:00:00,000 --> 00:00:05,000\nhello',encoding='utf-8')
        with patch('sys.argv', ['video_notes','https://example.org/v','--subtitle-file',str(f)]), patch('builtins.input',side_effect=AssertionError('question')), patch.dict('sys.modules',{'faster_whisper':None}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(),0)
        self.assertFalse(settings.SETTINGS_FILE.exists())

    def test_media_removed_text_still_reused_and_changed_model_misses(self):
        import hashlib
        url='https://example.org/v'
        base=self.root/'downloads'; folder=base/hashlib.sha256(url.encode()).hexdigest()[:16]; folder.mkdir(parents=True)
        clip=folder/'audio.m4a'; clip.write_bytes(b'audio')
        (folder/'media.json').write_text(json.dumps({'file':clip.name,'size':5}))
        engine=self.engine()
        with patch.dict('sys.modules',{'faster_whisper':engine}):
            first=download_and_transcribe(url,model='small',download_dir=base,model_dir=self.root/'models')
        with storage.media_lock(base):
            storage.cleanup_media(base,all_media=True)
        self.assertFalse(clip.exists())
        with patch('video_notes.media.yt_dlp.YoutubeDL',side_effect=AssertionError('network')), patch.dict('sys.modules',{'faster_whisper':None}):
            again=download_and_transcribe(url,model='small',download_dir=base,cache_only=True)
            self.assertTrue(again['cache']['reused'])
            self.assertFalse(again['media_available'])
            self.assertEqual(first['provenance'],again['provenance'])
            self.assertIsNone(download_and_transcribe(url,model='large-v3',download_dir=base,cache_only=True))
            self.assertIsNone(download_and_transcribe(url,model='small',beam_size=5,download_dir=base,cache_only=True))
            self.assertIsNone(download_and_transcribe(url,model='small',force=True,download_dir=base,cache_only=True))

    def test_cleanup_age_budget_keep_and_unknown_files(self):
        base=self.root/'downloads'; base.mkdir()
        for name, age in [('old',9),('new',0)]:
            folder=base/name;folder.mkdir();marker=folder/'.videonotes-media';marker.touch();(folder/'clip').write_bytes(b'1234567890')
            os.utime(marker,(1000000-age*86400,)*2)
        user=base/'user';user.mkdir();(user/'clip').write_bytes(b'user')
        with patch('video_notes.storage.policy',return_value={'keep_media':True}):
            self.assertEqual(storage.cleanup_media(base,now=1000000),[])
        with patch('video_notes.storage.policy',return_value={'keep_media':False}):
            storage.cleanup_media(base,now=1000000)
            self.assertFalse((base/'old').exists()); self.assertTrue((base/'new').exists())
            with patch('video_notes.storage.MAX_BYTES',1):storage.cleanup_media(base,now=1000000)
        self.assertFalse((base/'new').exists());self.assertTrue(user.exists())

    def test_media_lock_blocks_concurrent_cleanup(self):
        with storage.media_lock(self.root/'downloads'):
            with self.assertRaises(ValueError):
                with storage.media_lock(self.root/'downloads'):pass
        with storage.media_lock(self.root/'downloads'):pass

    def test_default_legacy_and_explicit_directory(self):
        root=self.root/'install';root.mkdir()
        with patch.dict(os.environ,{'LOCALAPPDATA':str(self.root/'local')}):
            self.assertEqual(paths.data_directory(root),self.root/'local/VideoNotes')
            (root/'data/models').mkdir(parents=True)
            self.assertEqual(paths.data_directory(root),root/'data')
            (root/'storage.json').write_text(json.dumps({'data_dir':str(self.root/'custom')}))
            self.assertEqual(paths.data_directory(root),self.root/'custom')

    def test_migrate_keeps_original_and_does_not_overwrite_conflict(self):
        old=self.root/'old';old.mkdir(); (old/'settings.json').write_text('{"model":"small"}')
        install=self.root/'install';install.mkdir()
        (install/'runtime.json').write_text('{}')
        target=self.root/'new'
        with patch('video_notes.storage.DATA_DIR',old),patch('video_notes.storage.DOWNLOAD_DIR',old/'downloads'),patch('video_notes.storage.INSTALL_DIR',install):
            storage.move_data(target)
            self.assertTrue((old/'settings.json').exists())
            self.assertEqual((target/'settings.json').read_bytes(),(old/'settings.json').read_bytes())
            self.assertEqual(json.loads((install/'storage.json').read_text())['data_dir'],str(target))
            conflict=self.root/'conflict'; conflict.mkdir();(conflict/'settings.json').write_text('different')
            with self.assertRaises(ValueError):storage.move_data(conflict)
            self.assertEqual((conflict/'settings.json').read_text(),'different')
            self.assertEqual(json.loads((install/'storage.json').read_text())['data_dir'],str(target))
