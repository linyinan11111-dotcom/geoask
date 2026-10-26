"""Copy canonical source assets into the wheel's resource package at build time."""
from pathlib import Path
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithResources(build_py):
    def run(self):
        super().run()
        root = Path(__file__).parent
        assets = [root / "config.yaml", root / "scripts/get_location.ps1", *sorted((root / "data").glob("*"))]
        for source in assets:
            if source.is_file():
                destination = Path(self.build_lib) / "geoask_resources" / source.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                self.copy_file(str(source), str(destination))


setup(cmdclass={"build_py": BuildWithResources})
