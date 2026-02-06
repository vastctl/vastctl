"""Tests for dependency detection."""

import pytest
from pathlib import Path

from vastctl_core.deps import DependencyDetector, DependencySpec


class TestDependencyDetector:
    """Test DependencyDetector auto-detection."""

    def test_detect_requirements_txt(self, tmp_path):
        """Test detection of requirements.txt."""
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("torch>=2.0\ntransformers\nnumpy")

        spec = DependencyDetector.detect(tmp_path)

        assert spec.requirements_file == reqs
        assert spec.packages == []  # Not parsed, just file reference

    def test_detect_pyproject_toml(self, tmp_path):
        """Test detection of pyproject.toml dependencies."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
dependencies = ["torch", "numpy>=1.20", "pandas"]
""")

        spec = DependencyDetector.detect(tmp_path)

        assert spec.pyproject_file == pyproject
        assert "torch" in spec.packages
        assert "numpy>=1.20" in spec.packages
        assert "pandas" in spec.packages

    def test_detect_pipfile(self, tmp_path):
        """Test detection of Pipfile."""
        pipfile = tmp_path / "Pipfile"
        pipfile.write_text("""
[packages]
torch = "*"
numpy = ">=1.20"
""")

        spec = DependencyDetector.detect(tmp_path)

        assert spec.pipfile == pipfile

    def test_requirements_takes_priority(self, tmp_path):
        """Test requirements.txt has priority over pyproject.toml."""
        (tmp_path / "requirements.txt").write_text("torch")
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["numpy"]')

        spec = DependencyDetector.detect(tmp_path)

        assert spec.requirements_file is not None
        assert spec.pyproject_file is None

    def test_pyproject_over_pipfile(self, tmp_path):
        """Test pyproject.toml has priority over Pipfile."""
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["torch"]')
        (tmp_path / "Pipfile").write_text('[packages]\nnumpy = "*"')

        spec = DependencyDetector.detect(tmp_path)

        assert spec.pyproject_file is not None
        assert spec.pipfile is None

    def test_no_dependencies(self, tmp_path):
        """Test empty project directory."""
        spec = DependencyDetector.detect(tmp_path)

        assert spec.is_empty()
        assert spec.requirements_file is None
        assert spec.pyproject_file is None
        assert spec.pipfile is None


class TestDependencySpecInstallCommand:
    """Test install command generation."""

    def test_install_cmd_requirements(self):
        """Test install command for requirements file."""
        spec = DependencySpec(requirements_file=Path("requirements.txt"))

        cmd = spec.install_command()

        assert cmd == "pip install -r requirements.txt"

    def test_install_cmd_packages(self):
        """Test install command for package list."""
        spec = DependencySpec(packages=["torch>=2.0", "numpy", "pandas"])

        cmd = spec.install_command()

        assert "pip install" in cmd
        assert "torch>=2.0" in cmd
        assert "numpy" in cmd
        assert "pandas" in cmd

    def test_install_cmd_pyproject(self):
        """Test install command for pyproject.toml."""
        spec = DependencySpec(pyproject_file=Path("pyproject.toml"))

        cmd = spec.install_command()

        # Should install from the project itself
        assert cmd == "pip install -e ."

    def test_install_cmd_empty(self):
        """Test install command when no dependencies."""
        spec = DependencySpec()

        cmd = spec.install_command()

        assert cmd == ""


class TestPyprojectParsing:
    """Test pyproject.toml dependency extraction."""

    def test_parse_standard_dependencies(self, tmp_path):
        """Test parsing standard [project] dependencies."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
name = "my-project"
dependencies = [
    "torch>=2.0",
    "transformers",
    "numpy>=1.20,<2.0",
]
""")

        spec = DependencyDetector.detect(tmp_path)

        assert "torch>=2.0" in spec.packages
        assert "transformers" in spec.packages
        assert "numpy>=1.20,<2.0" in spec.packages

    def test_parse_optional_dependencies(self, tmp_path):
        """Test that optional dependencies are NOT included by default."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
dependencies = ["torch"]

[project.optional-dependencies]
dev = ["pytest", "black"]
""")

        spec = DependencyDetector.detect(tmp_path)

        assert "torch" in spec.packages
        assert "pytest" not in spec.packages

    def test_parse_empty_dependencies(self, tmp_path):
        """Test pyproject with no dependencies section."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""
[project]
name = "my-project"
version = "0.1.0"
""")

        spec = DependencyDetector.detect(tmp_path)

        # Should still detect pyproject but have empty packages
        assert spec.pyproject_file == pyproject
        assert spec.packages == []
