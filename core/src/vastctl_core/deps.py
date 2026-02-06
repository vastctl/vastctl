"""Dependency detection for training projects."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

try:
    import tomllib
except ImportError:
    import tomli as tomllib


@dataclass
class DependencySpec:
    """Specification of project dependencies."""

    requirements_file: Optional[Path] = None
    pyproject_file: Optional[Path] = None
    pipfile: Optional[Path] = None
    packages: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Check if no dependencies were detected."""
        return (
            self.requirements_file is None
            and self.pyproject_file is None
            and self.pipfile is None
            and not self.packages
        )

    def install_command(self) -> str:
        """Generate pip install command for these dependencies."""
        if self.requirements_file:
            return f"pip install -r {self.requirements_file.name}"
        elif self.packages:
            packages_str = " ".join(f'"{p}"' if " " in p else p for p in self.packages)
            return f"pip install {packages_str}"
        elif self.pyproject_file:
            return "pip install -e ."
        elif self.pipfile:
            return "pipenv install"
        return ""


class DependencyDetector:
    """Auto-detect project dependencies from common files."""

    @staticmethod
    def detect(project_dir: Path) -> DependencySpec:
        """Detect dependencies from project files.

        Priority order: requirements.txt > pyproject.toml > Pipfile

        Args:
            project_dir: Path to the project directory

        Returns:
            DependencySpec with detected dependencies
        """
        spec = DependencySpec()

        # Check requirements.txt first (highest priority)
        requirements_file = project_dir / "requirements.txt"
        if requirements_file.exists():
            spec.requirements_file = requirements_file
            return spec

        # Check pyproject.toml
        pyproject_file = project_dir / "pyproject.toml"
        if pyproject_file.exists():
            spec.pyproject_file = pyproject_file
            spec.packages = DependencyDetector._parse_pyproject(pyproject_file)
            return spec

        # Check Pipfile
        pipfile = project_dir / "Pipfile"
        if pipfile.exists():
            spec.pipfile = pipfile
            return spec

        return spec

    @staticmethod
    def _parse_pyproject(pyproject_path: Path) -> List[str]:
        """Extract dependencies from pyproject.toml.

        Args:
            pyproject_path: Path to pyproject.toml

        Returns:
            List of dependency strings
        """
        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)

            # Get standard dependencies from [project] section
            project = data.get("project", {})
            deps = project.get("dependencies", [])

            return list(deps)

        except Exception:
            return []
