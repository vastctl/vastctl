"""Provisioning configuration for VastLab instances.

Extracts onstart script building logic from cli.py for maintainability
and adds support for fast/standard provisioning modes with config-driven packages.

Supports both Config-based usage and dict-based usage (for profiles).
"""

from typing import Any, Dict, Optional, List
from .config import Config


def get_torch_install_cmd(gpu_type: str, is_cpu_only: bool, torch_mode: str = "auto") -> str:
    """Get the appropriate PyTorch install command for the GPU type.

    Args:
        gpu_type: GPU type string (e.g., "A100", "RTX 5090")
        is_cpu_only: Whether this is a CPU-only instance
        torch_mode: One of: skip, auto, cpu, cu124, cu128-nightly

    Returns:
        pip install command for PyTorch (or empty string if skip/auto)
    """
    if torch_mode == "skip":
        return ""

    if torch_mode == "auto":
        # Auto mode: check if torch is installed and working, skip if so
        # CPU-only: just check if import succeeds (no CUDA check)
        # GPU: check if torch is installed AND CUDA is available
        if is_cpu_only:
            return '''
# Auto torch mode (CPU): skip if already installed
if python -c "import torch; print('ok')" 2>/dev/null | grep -q "ok"; then
    echo "PyTorch already installed, skipping upgrade"
else
    echo "Installing PyTorch (CPU)..."
    ''' + _get_torch_pip_cmd(gpu_type, is_cpu_only) + '''
fi
'''
        else:
            return '''
# Auto torch mode (GPU): skip if installed with CUDA support
if python -c "import torch; print(torch.cuda.is_available())" 2>/dev/null | grep -q "True"; then
    echo "PyTorch already installed with CUDA support, skipping upgrade"
else
    echo "Installing/upgrading PyTorch..."
    ''' + _get_torch_pip_cmd(gpu_type, is_cpu_only) + '''
fi
'''

    if torch_mode == "cpu" or is_cpu_only:
        return "python -m pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu"
    elif torch_mode == "cu128-nightly" or '5090' in gpu_type.lower():
        return "python -m pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128"
    elif torch_mode == "cu124":
        return "python -m pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124"
    else:
        # Default to CUDA 12.4 for most GPUs
        return "python -m pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124"


def _get_torch_pip_cmd(gpu_type: str, is_cpu_only: bool) -> str:
    """Get raw torch pip install command without auto-detection wrapper."""
    if is_cpu_only:
        return "python -m pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu"
    elif '5090' in gpu_type.lower():
        return "python -m pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128"
    else:
        return "python -m pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124"


def get_packages_cmd(config: Config, fast: bool = False) -> str:
    """Get the pip install command for packages from config.

    Args:
        config: VastLab Config object
        fast: If True, use minimal package set

    Returns:
        pip install command string
    """
    if fast:
        packages = config.get('provisioning.pip.fast_packages', ['jupyterlab', 'notebook'])
    else:
        packages = config.get('provisioning.pip.packages', [])

    if not packages:
        return ""

    return f"python -m pip install -q -U {' '.join(packages)}"


def get_apt_packages_cmd(config: Config) -> str:
    """Get the apt install command for packages from config.

    Args:
        config: VastLab Config object

    Returns:
        apt install command string
    """
    packages = config.get('provisioning.apt.packages', ['zip', 'unzip'])

    if not packages:
        return ""

    pkg_list = ' '.join(packages)
    # Always install - don't skip based on first package existence
    # DPKG will skip already-installed packages automatically
    return f"apt-get update && apt-get install -y {pkg_list}"


def get_jupyter_start_cmd(jupyter_token: str) -> str:
    """Get the Jupyter Lab startup command.

    Args:
        jupyter_token: Token for Jupyter authentication

    Returns:
        Jupyter startup command string
    """
    return f'''echo "setup_complete=true" > /root/.vastlab_setup
jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --NotebookApp.token='{jupyter_token}' --NotebookApp.password='' --notebook-dir=. &
'''


def get_logging_functions(config: Config) -> str:
    """Get logging function definitions (without tee activation).

    SECURITY: This defines logging helpers but does NOT start tee.
    Secrets can be written before tee is enabled.

    Args:
        config: VastLab Config object

    Returns:
        Logging function definitions
    """
    if not config.get('provisioning.logging.enabled', True):
        return ""

    log_file = config.get('provisioning.logging.log_file', '/root/vastlab_onstart.log')
    status_file = config.get('provisioning.logging.status_file', '/root/.vastlab_setup.json')

    return f'''
# VastLab provisioning logging (functions only - tee enabled later)
LOG_FILE="{log_file}"
STATUS_FILE="{status_file}"

log_phase() {{
    local phase="$1"
    local ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "{{\\"phase\\":\\"$phase\\",\\"ts\\":\\"$ts\\"}}" > "$STATUS_FILE"
    echo "[$(date)] === Phase: $phase ==="
}}
'''


def enable_logging_tee() -> str:
    """Get command to enable log capture via tee.

    SECURITY: Call this AFTER secret injection steps to avoid
    logging sensitive environment variables.

    Returns:
        Shell command to enable tee logging
    """
    return '''
# Enable log capture AFTER secret injection steps
exec > >(tee -a "$LOG_FILE") 2>&1
'''


def get_logging_setup(config: Config) -> str:
    """Get logging setup commands if enabled in config.

    DEPRECATED: Use get_logging_functions() + enable_logging_tee() for security.
    This function is kept for backwards compatibility.

    Args:
        config: VastLab Config object

    Returns:
        Logging setup commands (functions + tee)
    """
    funcs = get_logging_functions(config)
    if not funcs:
        return ""
    return funcs + enable_logging_tee() + '\nlog_phase "init"\n'


def log_phase_cmd(phase: str) -> str:
    """Get command to log a phase (if logging is enabled)."""
    return f'log_phase "{phase}"'


# =============================================================================
# Dict-based helpers (for profile support)
# =============================================================================


def get_packages_cmd_from_prov(prov: Dict[str, Any], fast: bool = False) -> str:
    """Get pip install command from a provisioning dict.

    Args:
        prov: Provisioning configuration dict
        fast: If True, use minimal package set

    Returns:
        pip install command string
    """
    pip_cfg = prov.get("pip") or {}
    if fast:
        packages = pip_cfg.get("fast_packages") or ["jupyterlab", "notebook"]
    else:
        packages = pip_cfg.get("packages") or []

    if not packages:
        return ""

    return f"python -m pip install -q -U {' '.join(packages)}"


def get_apt_packages_cmd_from_prov(prov: Dict[str, Any]) -> str:
    """Get apt install command from a provisioning dict.

    Args:
        prov: Provisioning configuration dict

    Returns:
        apt install command string
    """
    apt_cfg = prov.get("apt") or {}
    packages = apt_cfg.get("packages") or []

    if not packages:
        return ""

    pkg_list = " ".join(packages)
    # Always install - don't skip based on first package existence
    # DPKG will skip already-installed packages automatically
    return f"apt-get update && apt-get install -y {pkg_list}"


def get_torch_mode_from_prov(prov: Dict[str, Any]) -> str:
    """Get torch mode from a provisioning dict.

    Args:
        prov: Provisioning configuration dict

    Returns:
        Torch mode string (skip, auto, cpu, cu124, cu128-nightly)
    """
    torch_cfg = prov.get("torch") or {}
    return torch_cfg.get("mode", "auto")


def get_logging_functions_from_prov(prov: Dict[str, Any]) -> str:
    """Get logging function definitions from a provisioning dict (without tee).

    SECURITY: This defines logging helpers but does NOT start tee.

    Args:
        prov: Provisioning configuration dict

    Returns:
        Logging function definitions
    """
    log_cfg = prov.get("logging") or {}
    if not log_cfg.get("enabled", True):
        return ""

    log_file = log_cfg.get("log_file", "/root/vastlab_onstart.log")
    status_file = log_cfg.get("status_file", "/root/.vastlab_setup.json")

    return f'''
# VastLab provisioning logging (functions only - tee enabled later)
LOG_FILE="{log_file}"
STATUS_FILE="{status_file}"

log_phase() {{
    local phase="$1"
    local ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "{{\\"phase\\":\\"$phase\\",\\"ts\\":\\"$ts\\"}}" > "$STATUS_FILE"
    echo "[$(date)] === Phase: $phase ==="
}}
'''


def get_logging_setup_from_prov(prov: Dict[str, Any]) -> str:
    """Get logging setup commands from a provisioning dict.

    DEPRECATED: Use get_logging_functions_from_prov() + enable_logging_tee() for security.

    Args:
        prov: Provisioning configuration dict

    Returns:
        Logging setup commands (functions + tee)
    """
    funcs = get_logging_functions_from_prov(prov)
    if not funcs:
        return ""
    return funcs + enable_logging_tee() + '\nlog_phase "init"\n'


def get_custom_commands_from_prov(prov: Dict[str, Any]) -> str:
    """Get custom commands from a provisioning dict.

    Args:
        prov: Provisioning configuration dict

    Returns:
        Custom commands as newline-separated string
    """
    commands = prov.get("commands") or []
    if not commands:
        return ""
    return "\n".join(commands)


def build_onstart_script(
    config: Config,
    *,
    jupyter_token: str,
    provisioning: Optional[Dict[str, Any]] = None,
    env_setup_cmd: str = "",
    auto_env_cmd: str = "",
    workspace_cmd: str = "",
    is_cpu_only: bool = False,
    gpu_type: str = "",
    fast: bool = False,
    custom_packages: Optional[List[str]] = None,
    skip_torch: bool = False,
) -> str:
    """Build the complete onstart script for instance provisioning.

    SECURITY: Env injection happens BEFORE tee logging is enabled to prevent
    secrets from being written to log files.

    Args:
        config: VastLab Config object
        jupyter_token: Token for Jupyter authentication
        provisioning: Effective provisioning dict (from ProfileStore). If provided,
            uses dict-based helpers. If None, reads from config directly.
        env_setup_cmd: Command to setup environment variables
        auto_env_cmd: Command for auto-detected env injection
        workspace_cmd: Command to setup workspace directory
        is_cpu_only: Whether this is a CPU-only instance
        gpu_type: GPU type string for torch install selection
        fast: If True, skip heavy installs (packages + torch)
        custom_packages: Custom list of packages to install (overrides config)
        skip_torch: If True, skip PyTorch installation entirely

    Returns:
        Complete onstart script as a string
    """
    # Use provisioning dict if provided, otherwise fall back to config
    use_prov_dict = provisioning is not None
    prov = provisioning or {}

    # Determine torch mode
    if use_prov_dict:
        torch_mode = get_torch_mode_from_prov(prov)
    else:
        torch_mode = config.get('provisioning.torch.mode', 'auto')

    if fast or skip_torch:
        torch_mode = 'skip'

    # Build script parts
    parts = []

    # ==========================================================================
    # PHASE 0: Strict mode and Python bootstrap (CRITICAL)
    # ==========================================================================
    parts.append('''#!/bin/bash
set -e  # Exit on any error - fail fast, don't silently continue

# =============================================================================
# Python Bootstrap (unconditional - required for all provisioning)
# =============================================================================
# This ensures python/pip exist BEFORE any python commands run.
# Even on pytorch images, python-is-python3 may be missing.
apt-get update
apt-get install -y python3 python3-pip python-is-python3

# Sanity check - fail early if Python is broken
python --version || { echo "FATAL: python not available"; exit 1; }
python -m pip --version || { echo "FATAL: pip not available"; exit 1; }
echo "Python bootstrap complete"
''')

    # ==========================================================================
    # PHASE 1: Logging functions (no tee yet - safe for secrets)
    # ==========================================================================
    if use_prov_dict:
        logging_funcs = get_logging_functions_from_prov(prov)
    else:
        logging_funcs = get_logging_functions(config)

    if logging_funcs:
        parts.append(logging_funcs)

    # ==========================================================================
    # PHASE 2: SECRET INJECTION (before tee - never logged)
    # ==========================================================================
    # Environment file injection (contains secrets)
    if env_setup_cmd.strip():
        parts.append(env_setup_cmd.strip())

    # Auto-detected env injection (contains secrets)
    if auto_env_cmd.strip():
        parts.append(auto_env_cmd.strip())

    # ==========================================================================
    # PHASE 3: Enable tee logging (safe from here on)
    # ==========================================================================
    if logging_funcs:
        parts.append(enable_logging_tee())
        parts.append('log_phase "init"')

    # ==========================================================================
    # PHASE 4: Normal provisioning (logged)
    # ==========================================================================

    # APT packages
    if use_prov_dict:
        apt_cmd = get_apt_packages_cmd_from_prov(prov)
    else:
        apt_cmd = get_apt_packages_cmd(config)

    if apt_cmd:
        if logging_funcs:
            parts.append(log_phase_cmd("apt_packages"))
        parts.append(apt_cmd)

    # Workspace setup
    if workspace_cmd.strip():
        if logging_funcs:
            parts.append(log_phase_cmd("workspace"))
        parts.append(workspace_cmd.strip())

    # Pip packages
    if custom_packages:
        packages_cmd = f"python -m pip install -q -U {' '.join(custom_packages)}"
    elif use_prov_dict:
        packages_cmd = get_packages_cmd_from_prov(prov, fast=fast)
    else:
        packages_cmd = get_packages_cmd(config, fast=fast)

    if packages_cmd:
        if logging_funcs:
            parts.append(log_phase_cmd("pip_packages"))
        parts.append(packages_cmd)

    # Jupyter startup
    if logging_funcs:
        parts.append(log_phase_cmd("jupyter"))
    parts.append(get_jupyter_start_cmd(jupyter_token))

    # PyTorch installation (last, takes longest)
    torch_cmd = get_torch_install_cmd(gpu_type, is_cpu_only, torch_mode)
    if torch_cmd.strip():
        if logging_funcs:
            parts.append(log_phase_cmd("torch"))
        parts.append(torch_cmd.strip())

    # Custom commands from profile (after standard setup)
    if use_prov_dict:
        custom_cmds = get_custom_commands_from_prov(prov)
        if custom_cmds.strip():
            if logging_funcs:
                parts.append(log_phase_cmd("custom_commands"))
            parts.append(custom_cmds.strip())

    # Final phase
    if logging_funcs:
        parts.append(log_phase_cmd("complete"))

    return "\n".join(parts)
