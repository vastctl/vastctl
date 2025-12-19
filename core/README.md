# vastctl-core

Core Python library for managing GPU instances on [Vast.ai](https://vast.ai). Use this package to programmatically search, provision, and manage cloud GPU instances.

## Installation

```bash
pip install vastctl-core
```

## Quick Start

```python
from vastctl_core import Config, VastAPI, Registry

# Load configuration
config = Config()

# Search for GPU offers
with VastAPI(config.api_key) as api:
    offers = api.search_offers("A100", num_gpus=1)

    for offer in offers[:5]:
        print(f"${offer['dph_total']:.2f}/hr - {offer['gpu_name']} - {offer['gpu_ram']}GB")
```

## Features

- **Search & Filter**: Find GPU instances by type, VRAM, price, bandwidth, reliability
- **Instance Management**: Create, start, stop, and destroy instances programmatically
- **Connection Handling**: SSH connections, tunneling, Jupyter integration
- **File Transfer**: Upload/download files, create backups
- **Environment Injection**: Securely inject credentials via SSH (never exposed to Vast API)
- **Provisioning Profiles**: Reusable setup configurations for different workloads

## Usage Examples

### Search for Offers

```python
from vastctl_core import VastAPI

with VastAPI("your-api-key") as api:
    # Search for 4x A100 with minimum 400 Mbps bandwidth
    offers = api.search_offers(
        gpu_type="A100",
        num_gpus=4,
        min_bandwidth_mbps=400,
        max_price_per_gpu=1.50
    )

    print(f"Found {len(offers)} matching offers")
```

### Provision an Instance

```python
from vastctl_core import Config, VastAPI, Registry, Instance

config = Config()
registry = Registry(config)

with VastAPI(config.api_key) as api:
    # Find best offer
    offers = api.search_offers("RTX4090", num_gpus=1)
    offer = offers[0]

    # Create instance
    result = api.create_instance(
        offer_id=offer['id'],
        disk_gb=100,
        image="pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime",
        label="my-training-job"
    )

    # Track locally
    instance = Instance(
        name="my-training-job",
        vast_id=result['new_contract'],
        gpu_type=offer['gpu_name'],
        gpu_count=1
    )
    registry.add(instance)
```

### Execute Remote Commands

```python
from vastctl_core import Config, Registry, ConnectionManager

config = Config()
registry = Registry(config)
connection = ConnectionManager(config)

instance = registry.get("my-instance")

# Run a command
output, error = connection.execute_remote_command(
    instance,
    "nvidia-smi --query-gpu=name,memory.total --format=csv"
)
print(output)
```

### Inject Environment Variables

```python
from vastctl_core import Config, Registry, ConnectionManager
from vastctl_core.auto_env import scrape_credential_env_vars

config = Config()
registry = Registry(config)
connection = ConnectionManager(config)

instance = registry.get("my-instance")

# Auto-detect local credentials (AWS_*, WANDB_*, HF_*, etc.)
credentials = scrape_credential_env_vars()

# Securely inject via SSH (never touches Vast API)
connection.inject_auto_env(instance, credentials)
```

## Configuration

Configuration is stored in `~/.config/vastctl/config.yaml`:

```yaml
api_key: your-vast-api-key
default_gpu_type: A100
default_disk_gb: 200
default_image: pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime

ssh_key_path: ~/.ssh/vast_rsa

defaults:
  bandwidth_min: 400
  reliability_min: 0.95
  price_max: 3.0
```

## API Reference

### VastAPI

| Method | Description |
|--------|-------------|
| `search_offers(gpu_type, num_gpus, ...)` | Search available GPU offers |
| `search_cpu_offers(min_cpus, min_ram_gb, ...)` | Search CPU-only offers |
| `create_instance(offer_id, disk_gb, image, ...)` | Provision a new instance |
| `show_instances()` | List your running instances |
| `get_instance(instance_id)` | Get instance details |
| `start_instance(instance_id)` | Start a stopped instance |
| `stop_instance(instance_id)` | Stop a running instance |
| `destroy_instance(instance_id)` | Permanently destroy instance |
| `get_ssh_info(instance_id)` | Get SSH connection details |

### ConnectionManager

| Method | Description |
|--------|-------------|
| `ssh_connect(instance, command)` | Open interactive SSH session |
| `execute_remote_command(instance, cmd)` | Run command and get output |
| `execute_command(instance, cmd)` | Run command (success/fail) |
| `setup_tunnel(instance, local_port, remote_port)` | Create SSH tunnel |
| `inject_env_file(instance, content)` | Inject .env file content |
| `inject_auto_env(instance, env_vars)` | Inject environment variables |

### StorageManager

| Method | Description |
|--------|-------------|
| `upload_file(instance, local, remote)` | Upload file to instance |
| `download_file(instance, remote, local)` | Download file from instance |
| `create_backup(instance, patterns)` | Backup instance workspace |
| `restore_backup(instance, backup_file)` | Restore from backup |

## License

Apache-2.0
