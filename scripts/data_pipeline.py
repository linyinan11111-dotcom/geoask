"""Verified source extraction and staged publication for data preparation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile
import zlib

from geoask import GeoaskError
from geoask_runtime import atomic_output

URL = 'https://download.geofabrik.de/asia/china/beijing-261020-free.gpkg.zip'
ARCHIVE_SHA256 = 'fe023075f9f587f8d6bcb58a48f9bc00bf5f487f7ab78334d93dd75899c3199b'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source_data(downloads, *, url=URL, expected_hash=ARCHIVE_SHA256):
    """Verify pinned archive and extracted CRC; never reuse a partial GPKG."""
    downloads = Path(downloads)
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / url.rsplit('/', 1)[-1]
    if not archive.is_file():
        print('Downloading', url, flush=True)
        with atomic_output(archive, mode='wb') as output:
            with urllib.request.urlopen(url, timeout=120) as response:
                shutil.copyfileobj(response, output)
            output.flush()
            if sha256(output.name) != expected_hash:
                raise GeoaskError('下载归档的 SHA256 与固定来源不一致，未发布文件；请核对来源日期及校验值。')
    if sha256(archive) != expected_hash:
        raise GeoaskError(f'缓存归档校验失败：{archive}。请移走损坏文件后重试；不要静默更换日期固定的数据。')
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = [i for i in bundle.infolist() if not i.is_dir() and i.filename.lower().endswith('.gpkg')]
            readmes = [i for i in bundle.infolist() if Path(i.filename).name.casefold() in {'readme', 'readme.txt'}]
            if len(members) != 1 or len(readmes) != 1:
                raise GeoaskError('来源归档布局已变化：需要唯一 GPKG 和 README；请检查上游文件后更新脚本。')
            info = members[0]
            # Stable cache path; never trust paths embedded in an archive.
            source = downloads / 'source' / 'beijing.gpkg'
            valid = False
            if source.is_file() and source.stat().st_size == info.file_size:
                checksum = 0
                with source.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        checksum = zlib.crc32(block, checksum)
                valid = checksum == info.CRC
            if not valid:
                with atomic_output(source, mode='wb', parents=True) as output, bundle.open(info) as contents:
                    shutil.copyfileobj(contents, output)  # ZipFile validates CRC on EOF.
            return archive, source, bundle.read(readmes[0])
    except (zipfile.BadZipFile, EOFError) as exc:
        raise GeoaskError('来源 ZIP 损坏或解压校验失败，请重新获取日期固定的归档。') from exc


def publish_dataset(generated, destination, report, manifest):
    """Stage JSON before data replacement; roll back data on publish failure.

    The pair cannot be made power-loss atomic on a normal filesystem. Consumers
    can detect an interrupted pair using the manifest's dataset SHA256.
    """
    generated, destination, manifest = map(Path, (generated, destination, manifest))
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.geoask-publish-') as folder:
        staged = Path(folder) / destination.name
        backup = Path(folder) / 'previous.gpkg'
        shutil.copyfile(generated, staged)
        existed = destination.exists()
        if existed:
            shutil.copyfile(destination, backup)
        published = False
        try:
            # Prepare and fsync JSON before exposing the new dataset.
            with atomic_output(manifest) as output:
                output.write(text)
                output.flush()
                os.fsync(output.fileno())
                os.replace(staged, destination)
                published = True
        except BaseException:
            if published:
                if existed:
                    os.replace(backup, destination)
                else:
                    destination.unlink(missing_ok=True)
            raise
