import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('retention',Path(__file__).parents[1]/'backup_retention.py')
retention=importlib.util.module_from_spec(spec);spec.loader.exec_module(retention)


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'backups';self.root.mkdir()
        self.stamp='20261111T150000Z'
        self.create(self.stamp)

    def create(self,stamp):
        (self.root/(stamp+'.postgres.dump')).write_bytes(b'PGDMP'+b'test-dump')
        for suffix,names in [('data.tar.gz',['catalog/file.txt']),('config.tar.gz',['fernet.key','.env'])]:
            with tarfile.open(self.root/(stamp+'.'+suffix),'w:gz') as archive:
                for name in names:
                    info=tarfile.TarInfo(name);info.size=4;archive.addfile(info,io.BytesIO(b'test'))
        retention.seal(self.root,stamp)

    def test_plan_never_deletes_and_apply_keeps_complete_replacement(self):
        self.create('20261110T030000Z')
        old=self.root/'before-pr156-20261111t090000z';old.mkdir()
        dump=old/'postgres.dump';dump.write_bytes(b'old');os.utime(dump,(1,1))
        patch=old/'tracked.patch';patch.write_text('preserve local changes')
        before=set(self.root.rglob('*'))
        planned=retention.plan(self.root,self.stamp)
        self.assertEqual(set(self.root.rglob('*')),before)
        self.assertEqual(len(planned['files']),5)
        self.assertEqual(retention.apply(self.root,self.stamp)['remove_bytes'],planned['remove_bytes'])
        self.assertTrue(patch.exists())
        for suffix in (*retention.PARTS,'verified.json'):
            self.assertTrue((self.root/(self.stamp+'.'+suffix)).exists())

    def test_missing_component_refuses_all_deletions(self):
        old=self.root/'20261110T030000Z.postgres.dump';old.write_bytes(b'old')
        (self.root/(self.stamp+'.config.tar.gz')).unlink()
        with self.assertRaises(FileNotFoundError):retention.apply(self.root,self.stamp)
        self.assertTrue(old.exists())

    def test_corrupted_component_refuses_all_deletions(self):
        old=self.root/'20261110T030000Z.data.tar.gz';old.write_bytes(b'old')
        (self.root/(self.stamp+'.postgres.dump')).write_bytes(b'PGDMPtampered')
        with self.assertRaises(ValueError):retention.apply(self.root,self.stamp)
        self.assertTrue(old.exists())

    def test_newer_unknown_and_temporary_files_are_preserved(self):
        paths=['20261112T030000Z.postgres.dump','20261110T030000Z.postgres.dump.tmp',
               'manual.dump','unrelated-project.tar.gz','latest-verified']
        for name in paths:(self.root/name).write_text('preserve')
        retention.apply(self.root,self.stamp)
        self.assertTrue(all((self.root/name).exists() for name in paths))

    def test_old_internal_dumps_only_after_the_new_full_snapshot(self):
        folder=Path(self.temp.name)/'data-backups';folder.mkdir()
        old=folder/'20261110-030000-123456.dump';old.write_bytes(b'old');os.utime(old,(1,1))
        new=folder/'20261111-160000-123456.dump';new.write_bytes(b'new')
        cutoff=retention.stamp_time(self.stamp);os.utime(new,(cutoff+60,cutoff+60))
        manual=folder/'portable.wbai';manual.write_bytes(b'preserve')
        retention.apply(self.root,self.stamp,folder)
        self.assertFalse(old.exists());self.assertTrue(new.exists() and manual.exists())

    def test_partial_set_cannot_be_sealed(self):
        with self.assertRaises(FileNotFoundError):retention.seal(self.root,'20261109T030000Z')

    def test_manifest_must_have_all_expected_components(self):
        path=self.root/(self.stamp+'.verified.json')
        data=json.loads(path.read_text());del data['parts']['config.tar.gz'];path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):retention.apply(self.root,self.stamp)

    def test_stamp_cannot_escape_backup_directory(self):
        for value in ('../other','20261111T150000Z/../other','20261399T150000Z'):
            with self.assertRaises(ValueError):retention.plan(self.root,value)

    @unittest.skipIf(os.name=='nt','Windows sandbox does not grant symlink creation')
    def test_symlinks_are_never_followed_or_deleted(self):
        target=Path(self.temp.name)/'unrelated';target.write_bytes(b'keep')
        link=self.root/'20261110T030000Z.postgres.dump';link.symlink_to(target)
        retention.apply(self.root,self.stamp)
        self.assertTrue(link.is_symlink() and target.exists())
        (self.root/(self.stamp+'.postgres.dump')).unlink()
        (self.root/(self.stamp+'.postgres.dump')).symlink_to(target)
        with self.assertRaises(ValueError):retention.apply(self.root,self.stamp)


if __name__=='__main__':unittest.main()
