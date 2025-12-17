# vastctl-cli

A powerful command-line interface for managing GPU instances on [Vast.ai](https://vast.ai). Simplifies the workflow of searching, provisioning, and connecting to cloud GPUs for ML training and inference.

## Installation

```bash
pip install vastctl-cli
```

## Quick Start

```bash
# Set your Vast.ai API key
vastctl config set api_key <your-api-key>

# Search for available GPUs
vastctl search -t A100 -g 1

# Start an instance
vastctl start -n mybox -t A100 -g 1 -d 100

# Connect to Jupyter
vastctl connect mybox

# SSH into the instance
vastctl ssh mybox

# When done, destroy it
vastctl kill mybox
```

## Features

- **Smart Search**: Find GPUs by type, count, price, bandwidth, reliability
- **One-Command Provisioning**: Start instances with sensible defaults
- **Auto-Credentials**: Automatically inject AWS, WandB, HuggingFace tokens
- **Jupyter Integration**: One command to open Jupyter in your browser
- **File Transfer**: Easy upload/download with `vastctl cp`
- **Backup & Restore**: Save your work before destroying instances
- **Provisioning Profiles**: Reusable configurations for different workloads

## Commands

### Instance Management

```bash
# Search for GPU offers
vastctl search -t A100 -g 4                    # 4x A100
vastctl search -t H100 -g 8 --max-price 2.50   # 8x H100, max $2.50/GPU/hr
vastctl search -t RTX4090 --min-bandwidth 500  # RTX 4090, 500+ Mbps

# Search for CPU-only instances
vastctl search-cpu --cpus 32 --ram 128         # 32 cores, 128GB RAM

# Start an instance
vastctl start -n training -t A100 -g 4 -d 200
vastctl start -n inference -t RTX4090 -g 1 --image nvidia/cuda:12.4-runtime

# List your instances
vastctl list
vastctl list --running                         # Only running
vastctl list --project research                # Filter by project

# Instance status
vastctl status mybox

# Stop (pause billing, keep disk)
vastctl stop mybox

# Kill (destroy permanently)
vastctl kill mybox

# Remove from local tracking only
vastctl remove mybox
```

### Connecting

```bash
# Open Jupyter in browser (sets up SSH tunnel automatically)
vastctl connect mybox

# SSH into instance
vastctl ssh mybox

# SSH with tmux session (attach or create)
vastctl ssh mybox --tmux

# SSH creating a new tmux window
vastctl ssh mybox --tmux-new

# Run a command remotely
vastctl run mybox "nvidia-smi"
vastctl run mybox "python train.py"
```

### File Transfer

```bash
# Upload file
vastctl cp ./model.py :remote/path/

# Download file
vastctl cp :remote/results.csv ./local/

# Upload directory
vastctl cp -r ./src :remote/project/

# Download directory
vastctl cp -r :remote/checkpoints ./local/
```

### Backup & Restore

```bash
# Backup current workspace
vastctl backup mybox

# List available backups
vastctl backups mybox

# Restore from backup
vastctl restore mybox
vastctl restore mybox --backup-file backup-2024-01-15.tar.gz
```

### Environment Variables

```bash
# Show auto-detected credentials (AWS, WandB, HF, OpenAI, etc.)
vastctl env local

# Inject credentials into running instance
vastctl env inject mybox --auto

# Inject from .env file
vastctl env inject mybox --env-file .env

# Detect instance environment
vastctl env detect mybox
```

### Configuration

```bash
# Show current config
vastctl config show

# Set values
vastctl config set api_key sk-xxx
vastctl config set default_gpu_type A100
vastctl config set defaults.bandwidth_min 400

# Get specific value
vastctl config get api_key

# Edit config file directly
vastctl config edit
```

### Profiles

```bash
# List available provisioning profiles
vastctl profiles list

# Show profile details
vastctl profiles show ml-training

# Pull profiles from cloud (if logged in)
vastctl profiles pull
```

## Configuration

Config file location: `~/.config/vastctl/config.yaml`

```yaml
# Required
api_key: your-vast-api-key

# SSH (generate with: ssh-keygen -t rsa -f ~/.ssh/vast_rsa)
ssh_key_path: ~/.ssh/vast_rsa

# Defaults for 'vastctl start'
default_gpu_type: A100
default_disk_gb: 200
default_image: pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime

# Search filters
defaults:
  bandwidth_min: 400      # Minimum Mbps
  reliability_min: 0.95   # 95%+ uptime
  price_max: 3.0          # Max $/hr per GPU

# Auto-provisioning
provisioning:
  pip:
    packages:
      - jupyterlab
      - wandb
      - transformers
  torch:
    mode: auto            # auto-detect CUDA version
  apt:
    packages:
      - zip
      - unzip
```

## Provisioning Profiles

Define reusable configurations in your config:

```yaml
provisioning_profiles:
  ml-training:
    description: "ML training setup with common libraries"
    pip:
      packages:
        - torch
        - transformers
        - datasets
        - wandb
        - tensorboard
    apt:
      packages:
        - htop
        - tmux

  inference:
    description: "Lightweight inference setup"
    pip:
      packages:
        - torch
        - transformers
        - fastapi
        - uvicorn
```

Use with:
```bash
vastctl start -n mybox -t A100 --template ml-training
```

## Environment Variables

vastctl auto-detects and can inject these credential patterns:

| Pattern | Examples |
|---------|----------|
| `AWS_*` | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY |
| `WANDB_*` | WANDB_API_KEY, WANDB_PROJECT |
| `HF_*` | HF_TOKEN, HF_HOME |
| `HUGGING_FACE_*` | HUGGING_FACE_HUB_TOKEN |
| `OPENAI_*` | OPENAI_API_KEY |
| `ANTHROPIC_*` | ANTHROPIC_API_KEY |

Credentials are injected via SSH directly to the instance - they never touch the Vast.ai API.

## Tips

### Generate SSH Key
```bash
ssh-keygen -t rsa -b 4096 -f ~/.ssh/vast_rsa -N ""
```

### Set Active Instance
```bash
vastctl use mybox          # Set as active
vastctl ssh                # Now works without name
vastctl connect            # Same
```

### Refresh Instance Status
```bash
vastctl refresh            # Sync with Vast.ai API
vastctl refresh mybox      # Refresh specific instance
```

### Restart Jupyter
```bash
vastctl restart-jupyter mybox
```

## License

MIT
