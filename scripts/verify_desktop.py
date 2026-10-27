"""Extract the release ZIP and execute its binary with isolated user storage."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from geoask_resources import __version__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--model-cache', type=Path)
    args = parser.parse_args()
    archive = args.archive.resolve()
    if archive.name != f'geoask-{__version__}-windows-x64.zip':
        raise ValueError(f'Expected a desktop archive for version {__version__}')
    with tempfile.TemporaryDirectory(prefix='geoask-release-') as folder:
        directory = Path(folder) / '解压测试'
        directory.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                if not (directory / name).resolve().is_relative_to(directory.resolve()):
                    raise ValueError('Unsafe ZIP member')
                if Path(name).name.lower() in {'connection.dat', '.env', 'hosts.yml'}:
                    raise ValueError('Private file in release')
            bundle.extractall(directory)
        executable = directory / 'geoask/geoask.exe'
        report_path = directory / 'binary-smoke.json'
        command = [str(executable), '--self-test', str(report_path)]
        if args.model_cache:
            command += ['--model-cache', str(args.model_cache.resolve())]
        env = os.environ.copy()
        for name in ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY']:
            env.pop(name, None)
        env['LOCALAPPDATA'] = str(directory / 'private-user-data')
        env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        process = subprocess.run(command, cwd=directory, env=env, timeout=240,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
        report['passed'] = bool(report.get('passed') and process.returncode == 0
                                and report.get('version') == __version__
                                and report.get('frozen') is True)
        report.update(verified_at=datetime.now(timezone.utc).isoformat(),
                      expected_version=__version__,
                      executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                      archive=archive.name, archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                      returncode=process.returncode, isolated_user_storage=True, outside_checkout=True,
                      embedding_source='existing local cache' if args.model_cache else 'not exercised',
                      code_sha256={p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                   [ROOT / 'geoask.py', ROOT / 'scripts/desktop_entry.py', ROOT / 'scripts/build_desktop.py']})
        (ROOT / 'docs/desktop-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if process.returncode or not report.get('passed'):
            raise SystemExit(1)


if __name__ == '__main__':
    main()
