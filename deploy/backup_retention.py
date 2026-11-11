#!/usr/bin/env python3
"""Rotate only known assistant backups, after verifying a complete replacement."""
import argparse
import hashlib
import json
import re
import stat
import tarfile
from datetime import datetime, timezone
from pathlib import Path

STAMP = re.compile(r'\d{8}T\d{6}Z')
PARTS = ('postgres.dump', 'data.tar.gz', 'config.tar.gz')


def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Not a regular backup file: ' + str(path))
    return info


def digest(path):
    regular(path)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def root_path(value):
    path = Path(value)
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Backup directory must be a real directory')
    return path.resolve()


def stamp_time(value):
    if not STAMP.fullmatch(value):
        raise ValueError('Invalid backup timestamp')
    return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()


def seal(root, stamp):
    """Caller has already run pg_restore --list; tar checks are repeated here."""
    root = root_path(root)
    stamp_time(stamp)
    parts = {}
    for suffix in PARTS:
        path = root / (stamp + '.' + suffix)
        info = regular(path)
        if not info.st_size:
            raise ValueError('Empty backup component')
        if suffix == 'postgres.dump':
            with path.open('rb') as stream:
                if stream.read(5) != b'PGDMP':
                    raise ValueError('Invalid PostgreSQL dump header')
        else:
            with tarfile.open(path, 'r:gz') as archive:
                names = {member.name.removeprefix('./') for member in archive}
                if suffix == 'config.tar.gz' and not {'fernet.key', '.env'}.issubset(names):
                    raise ValueError('Configuration backup is incomplete')
        parts[suffix] = {'bytes': info.st_size, 'sha256': digest(path)}
    manifest = {'version': 1, 'stamp': stamp, 'catalog_verified': True,
                'restore_tested': False, 'parts': parts}
    target = root / (stamp + '.verified.json')
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(manifest, indent=2))
    temporary.replace(target)
    pointer = root / 'latest-verified.tmp'
    pointer.write_text(stamp + '\n')
    pointer.replace(root / 'latest-verified')
    return manifest


def verify(root, stamp):
    root = root_path(root)
    stamp_time(stamp)
    path = root / (stamp + '.verified.json')
    regular(path)
    manifest = json.loads(path.read_text())
    if (manifest.get('stamp') != stamp or manifest.get('catalog_verified') is not True
            or set(manifest.get('parts', {})) != set(PARTS)):
        raise ValueError('A complete verified replacement is required')
    for suffix in PARTS:
        path = root / (stamp + '.' + suffix)
        if regular(path).st_size != manifest['parts'][suffix]['bytes'] or digest(path) != manifest['parts'][suffix]['sha256']:
            raise ValueError('Replacement backup changed: ' + str(path))
    return root


def plan(root, stamp, data_backups=None):
    root = verify(root, stamp)
    cutoff = stamp_time(stamp)
    candidates = []
    for path in root.iterdir():
        match = re.fullmatch(r'(\d{8}T\d{6}Z)\.(postgres\.dump|data\.tar\.gz|config\.tar\.gz|verified\.json)', path.name)
        if match and match[1] < stamp and not path.is_symlink():
            candidates.append(path)
        elif (re.fullmatch(r'before-pr\d+-\d{8}t\d{6}z', path.name)
                and path.is_dir() and not path.is_symlink()):
            old = path / 'postgres.dump'
            if old.exists() and not old.is_symlink() and regular(old).st_mtime < cutoff:
                candidates.append(old)
        elif path.name == 'before-fast83.dump' and not path.is_symlink() and regular(path).st_mtime < cutoff:
            candidates.append(path)
    if data_backups is not None:
        folder = root_path(data_backups)
        for path in folder.iterdir():
            if re.fullmatch(r'\d{8}-\d{6}-\d{6}\.dump', path.name) and not path.is_symlink() and regular(path).st_mtime < cutoff:
                candidates.append(path)
    result = []
    for path in sorted(candidates):
        info = regular(path)
        result.append({'path': str(path), 'bytes': info.st_size, 'mtime_ns': info.st_mtime_ns,
                       'inode': info.st_ino, 'device': info.st_dev})
    return {'keep': stamp, 'files': result, 'remove_bytes': sum(item['bytes'] for item in result)}


def apply(root, stamp, data_backups=None):
    result = plan(root, stamp, data_backups)
    # Validate every candidate before the first deletion. Never recursively
    # delete folders, follow links, remove working volumes, or touch newer files.
    for item in result['files']:
        info = regular(Path(item['path']))
        if (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev) != (
                item['bytes'], item['mtime_ns'], item['inode'], item['device']):
            raise ValueError('Backup changed while planning rotation')
    for item in result['files']:
        Path(item['path']).unlink()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['seal', 'plan', 'apply'])
    parser.add_argument('--root', default='/var/backups/wb-ai-assistant')
    parser.add_argument('--stamp', required=True)
    parser.add_argument('--data-backups')
    args = parser.parse_args()
    result = seal(args.root, args.stamp) if args.action == 'seal' else globals()[args.action](args.root, args.stamp, args.data_backups)
    print(json.dumps(result, indent=2))
