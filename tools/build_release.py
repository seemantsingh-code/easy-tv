"""Build shareable artifacts, stripping local account metadata from the source tarball.

Requires: pip install build; run from a Git checkout with intended release files staged.
No publishing, signing, TV operations or runtime credential reads occur here.
"""
import gzip
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, '-m', 'build'], cwd=ROOT, check=True)
    from tomllib import loads
    version = loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    stem = 'easy-tv-' + version
    source = ROOT / 'dist' / (stem.replace('easy-tv-', 'easy_tv-', 1) + '.tar.gz')
    # setuptools' sdist preserves the build user's uid/gid/name by default.
    # Normalize those fields before sharing the archive.
    content = io.BytesIO()
    with tarfile.open(source, 'r:gz') as original:
        with gzip.GzipFile(fileobj=content, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as clean:
                for member in original.getmembers():
                    payload = original.extractfile(member) if member.isfile() else None
                    member.uid = member.gid = 0
                    member.uname = member.gname = ''
                    member.mtime = 0
                    member.pax_headers = {}
                    clean.addfile(member, payload)
    source.write_bytes(content.getvalue())
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    names = [name for name in names if name]
    if not names or 'README.md' not in names:
        raise SystemExit('Stage intended source files with git add before creating a repository ZIP.')
    target = ROOT / 'dist' / (stem + '-repository.zip')
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            path = ROOT / name
            if path.is_symlink() or not path.is_file():
                raise SystemExit('Release input must be a regular file: ' + name)
            info = zipfile.ZipInfo(stem + '/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = (0o100755 if path.stat().st_mode & 0o111 else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    print('Created ' + target.name + ' and normalized source archive; review artifacts before publishing.')


if __name__ == '__main__':
    main()
