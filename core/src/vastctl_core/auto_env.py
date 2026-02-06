"""
Automatic environment detection and injection for vastlab.

Detects credential environment variables from the local shell and
generates injection scripts for remote instances.
"""
import os
from typing import Dict

# Environment variable prefixes to auto-detect and forward
CREDENTIAL_PREFIXES = [
    'AWS_',           # AWS credentials (also used for S3-compatible like B2)
    'B2_',            # Backblaze B2 credentials
    'WANDB_',         # Weights & Biases
    'HF_',            # Hugging Face
    'HUGGING_FACE_',  # Hugging Face (alternative)
    'WARPDATA_',      # Warpdata config
    'WARPDATASETS_',  # Warpdata/warpdatasets config
    'OPENAI_',        # OpenAI
    'ANTHROPIC_',     # Anthropic
    'COHERE_',        # Cohere
    'REPLICATE_',     # Replicate
]


def scrape_credential_env_vars() -> Dict[str, str]:
    """
    Scrape local environment for credential variables to forward.

    Returns:
        Dict mapping variable names to their values.
        Only includes non-empty values.
    """
    result = {}

    for key, value in os.environ.items():
        # Skip empty values
        if not value:
            continue

        # Check if matches any credential prefix
        for prefix in CREDENTIAL_PREFIXES:
            if key.startswith(prefix):
                result[key] = value
                break

    return result


def generate_env_injection_script(env_vars: Dict[str, str]) -> str:
    """
    Generate bash script to inject environment variables on remote instance.

    SECURITY: Sets restrictive permissions (600) on env files to prevent
    accidental exposure of secrets.

    Args:
        env_vars: Dict mapping variable names to values

    Returns:
        Bash script string that exports variables and persists them to .bashrc
    """
    if not env_vars:
        return ""

    lines = []
    lines.append("# === Auto-Injected Credentials (from local environment) ===")

    # Set restrictive umask for file creation
    lines.append("umask 077")
    lines.append("")

    # Create /root/.auto_env file with exports
    lines.append("cat > /root/.auto_env << 'VASTLAB_ENV_EOF'")

    for key, value in sorted(env_vars.items()):
        # Escape the value for bash
        # Using single quotes and escaping any single quotes in the value
        escaped_value = value.replace("'", "'\\''")
        lines.append(f"export {key}='{escaped_value}'")

    lines.append("VASTLAB_ENV_EOF")
    lines.append("")

    # Explicitly set restrictive permissions (umask may be overridden)
    lines.append("chmod 600 /root/.auto_env 2>/dev/null || true")
    lines.append("")

    # Source it immediately
    lines.append("source /root/.auto_env")
    lines.append("")

    # Add to .bashrc if not already there
    lines.append("# Ensure auto-load for future SSH sessions")
    lines.append("if ! grep -q 'source /root/.auto_env' /root/.bashrc 2>/dev/null; then")
    lines.append("    echo '' >> /root/.bashrc")
    lines.append("    echo '# Auto-injected credentials from vastlab' >> /root/.bashrc")
    lines.append("    echo 'source /root/.auto_env' >> /root/.bashrc")
    lines.append("fi")
    lines.append("# =========================================================")

    return "\n".join(lines)


