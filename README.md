# vastctl

A professional CLI and Python library for managing GPU instances on [Vast.ai](https://vast.ai).

> **Note:** This is a third-party tool, not affiliated with Vast.ai.

## Packages

| Package | Description | Install |
|---------|-------------|---------|
| [vastctl-cli](./cli) | Command-line interface | `pip install vastctl-cli` |
| [vastctl-core](./core) | Python library | `pip install vastctl-core` |

## Quick Start

```bash
# Install the CLI
pip install vastctl-cli

# Configure
vastctl config set api_key <your-vast-api-key>

# Find GPUs
vastctl search -t A100 -g 1

# Start an instance
vastctl start -n mybox -t A100 -g 1

# Connect
vastctl connect mybox

# Done? Destroy it
vastctl kill mybox
```

## Why vastctl?

- **Simple**: One command to provision, one to connect
- **Smart defaults**: Auto-configures Jupyter, injects credentials
- **Safe**: Secrets never touch Vast.ai API (injected via SSH)
- **Flexible**: Use CLI or import as a library

## Links

- [CLI Documentation](./cli/README.md)
- [Library Documentation](./core/README.md)
- [Vast.ai](https://vast.ai)

## License

MIT
