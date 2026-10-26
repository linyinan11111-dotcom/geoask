"""Build the Windows x64 portable ZIP; includes no user profile or model cache."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from geoask_resources import __version__
    import geoask as g
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("Build this package using Windows x64 Python.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = ROOT / 'build' / 'desktop'
    work.mkdir(parents=True, exist_ok=True)
    # DuckDB checks the extension signature/version again when the binary loads it.
    with g.connect_spatial() as con:
        version = con.execute('SELECT version()').fetchone()[0]
        target = con.execute('PRAGMA platform').fetchone()[0]
    extension = Path.home() / '.duckdb' / 'extensions' / version / target / 'spatial.duckdb_extension'
    if not extension.is_file():
        raise SystemExit(f'Cannot locate installed spatial extension: {extension}')
    assets = work / 'resources'
    assets.mkdir(exist_ok=True)
    licenses = assets / 'THIRD_PARTY_LICENSES'
    licenses.mkdir(exist_ok=True)
    inventory = []
    for distribution in metadata.distributions():
        name = distribution.metadata.get('Name', 'unknown')
        inventory.append({'name': name, 'version': distribution.version})
        for file in distribution.files or []:
            path = Path(str(file))
            if '.dist-info' not in str(path) or not any(part.lower().startswith(('license', 'copying', 'notice', 'authors')) for part in path.parts):
                continue
            source = Path(distribution.locate_file(file))
            if source.is_file():
                destination = licenses / name / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
    (assets / 'build-environment.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
               '--name', 'geoask', '--distpath', str(output / 'desktop'), '--workpath', str(work / 'pyinstaller'),
               '--specpath', str(work), '--paths', str(ROOT),
               '--copy-metadata', 'fastembed', '--copy-metadata', 'onnxruntime',
               '--collect-all', 'onnxruntime', '--collect-all', 'tokenizers',
               '--collect-submodules', 'fastembed',
               '--add-data', f'{ROOT / "config.yaml"};geoask_resources',
               '--add-data', f'{ROOT / "data"};geoask_resources/data',
               '--add-data', f'{ROOT / "scripts/get_location.ps1"};geoask_resources/scripts',
               '--add-binary', f'{extension};geoask_resources']
    for name in ['torch', 'tensorflow', 'transformers', 'pandas', 'scipy', 'matplotlib', 'IPython', 'pytest']:
        command += ['--exclude-module', name]
    command.append(str(ROOT / 'scripts/desktop_entry.py'))
    subprocess.run(command, cwd=ROOT, check=True)
    application = output / 'desktop' / 'geoask'
    for name in ['LICENSE', 'README.md']:
        shutil.copyfile(ROOT / name, application / name)
    shutil.copyfile(ROOT / 'docs/release-download.md', application / '使用说明.md')
    shutil.copytree(licenses, application / 'THIRD_PARTY_LICENSES', dirs_exist_ok=True)
    shutil.copyfile(assets / 'build-environment.json', application / 'build-environment.json')
    # The Python runtime is from Anaconda; retain its upstream license too.
    runtime_license = Path(sys.base_prefix) / 'LICENSE_PYTHON.txt'
    if runtime_license.is_file():
        shutil.copyfile(runtime_license, application / 'PYTHON_LICENSE.txt')
    archive = output / f'geoask-{__version__}-windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(application.rglob('*')):
            if path.is_file():
                bundle.write(path, Path('geoask') / path.relative_to(application))
    print(f'Windows download: {archive} ({archive.stat().st_size / 1024 / 1024:.1f} MiB)')


if __name__ == '__main__':
    main()
