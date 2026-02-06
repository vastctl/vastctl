"""Pytest configuration and shared fixtures."""

import pytest
import sys
from pathlib import Path

# Add core and cli packages to path for testing
root = Path(__file__).parent.parent
sys.path.insert(0, str(root / "core" / "src"))
sys.path.insert(0, str(root / "cli" / "src"))
