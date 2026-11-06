# Antechamber — Copyright (c) 2026 House of Solance. PolyForm Noncommercial 1.0.0. Required Notice: Copyright (c) 2026 House of Solance (https://github.com/SolanceLab)
"""Ships engine/ inside the package as antechamber_launch._engine, without its tests."""
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def find_package_modules(self, package, package_dir):
        return [m for m in super().find_package_modules(package, package_dir) if not m[1].startswith("test_")]


setup(cmdclass={"build_py": BuildPy})
