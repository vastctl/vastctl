"""Instance management commands for VastLab CLI."""

import sys
import time
import subprocess
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
import humanize
from datetime import datetime

from vastctl_core import Config, Registry, Instance, ConnectionManager, StorageManager
from vastctl_core.vast_api import VastAPI, VastApiError
from vastctl_core.auto_env import scrape_credential_env_vars
from vastctl_core.profiles import ProfileStore
from vastctl_core.provisioning import build_onstart_script

from ..context import CliContext

console = Console()
pass_ctx = click.make_pass_decorator(CliContext, ensure=True)


# GPU options for interactive selection
GPU_OPTIONS = [
    ("A100", "NVIDIA A100 - Best for large models and training", "80GB/40GB HBM2e"),
    ("H200", "NVIDIA H200 - Latest Hopper architecture", "141GB HBM3e"),
    ("RTX 5090", "NVIDIA RTX 5090 - Great price/performance", "32GB GDDR7"),
    ("H100", "NVIDIA H100 - Previous gen flagship", "80GB HBM3"),
    ("RTX 4090", "NVIDIA RTX 4090 - Consumer flagship", "24GB GDDR6X"),
    ("RTX 3090", "NVIDIA RTX 3090 - Budget option", "24GB GDDR6X"),
    ("Other", "Enter custom GPU type", "")
]


def ensure_ssh_key_exists(config) -> bool:
    """Ensure SSH key exists, auto-generate if missing.

    Returns True if key exists (or was created), False on failure.
    """
    ssh_key_path = config.ssh_key_path
    ssh_pub_path = config.ssh_public_key_path

    if ssh_key_path.exists() and ssh_pub_path.exists():
        return True

    console.print("[yellow]No SSH key found, generating one...[/yellow]")
    try:
        # Generate new SSH key pair
        result = subprocess.run([
            "ssh-keygen", "-t", "ed25519",
            "-f", str(ssh_key_path),
            "-N", "",  # No passphrase
            "-C", "vastctl-auto-generated"
        ], capture_output=True, text=True)

        if result.returncode == 0:
            console.print(f"[green]✓[/green] Generated SSH key: {ssh_key_path}")
            return True
        else:
            console.print(f"[red]Failed to generate SSH key: {result.stderr}[/red]")
            return False
    except Exception as e:
        console.print(f"[red]Failed to generate SSH key: {e}[/red]")
        return False


def ensure_ssh_key_attached(api, instance_id: int, ssh_pub: str, retries: int = 5, delay: int = 5) -> bool:
    """Attach SSH key with retries (Vast.ai timing workaround).

    Returns True if key was attached successfully.
    """
    for attempt in range(retries):
        try:
            api.attach_ssh_key(instance_id, ssh_pub)
            return True
        except VastApiError as e:
            # "already associated" is success
            if "already associated" in str(e).lower():
                return True
            if attempt < retries - 1:
                time.sleep(delay)
        except Exception:
            if attempt < retries - 1:
                time.sleep(delay)
    return False


def wait_for_ssh_ready(config, instance, timeout: int = 120) -> bool:
    """Wait for SSH to be actually ready using subprocess ssh.

    Uses the same method as execute_command (subprocess ssh) to ensure
    consistency. Paramiko and subprocess ssh can behave differently.

    Returns True if SSH connection succeeds within timeout.
    """
    start = time.time()
    while time.time() - start < timeout:
        try:
            # Use subprocess ssh (same as execute_command) for consistency
            ssh_cmd = [
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=10",
                "-o", "BatchMode=yes",
                "-i", str(config.ssh_key_path),
                "-p", str(instance.ssh_port),
                f"root@{instance.ssh_host}",
                "echo 'ssh_ready'"
            ]
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and "ssh_ready" in result.stdout:
                return True
        except Exception:
            pass
        time.sleep(5)
    return False


def print_ssh_diagnostic(instance_name: str, config=None, stderr: str = ""):
    """Print helpful SSH troubleshooting info."""
    # Get actual key path from config if available
    pub_key_path = "~/.ssh/id_ed25519.pub"
    if config:
        pub_key_path = str(config.ssh_public_key_path)

    console.print(f"""
[red]SSH authentication failed[/red]

This usually means:
  • SSH key was not attached yet (Vast.ai timing issue)
  • Wrong ssh_key_path in config
  • Key was created after instance started

[yellow]Quick fix:[/yellow]
  vastctl stop {instance_name}
  vastctl start -n {instance_name}

[yellow]Or manually attach key:[/yellow]
  1. Run: cat {pub_key_path}
  2. Go to console.vast.ai → Instances → {instance_name} → SSH Keys
  3. Paste your public key and attach
  4. Wait 30 seconds, then: vastctl ssh {instance_name}
""")


def prompt_gpu_type() -> str:
    """Interactive GPU type selection menu."""
    console.print("\n[bold]Select GPU type:[/bold]")

    for i, (gpu, desc, mem) in enumerate(GPU_OPTIONS, 1):
        if mem:
            console.print(f"  [{i}] {gpu:<12} - {desc} ({mem})")
        else:
            console.print(f"  [{i}] {gpu:<12} - {desc}")

    choice = click.prompt(f"\nEnter choice (1-{len(GPU_OPTIONS)})", type=int)

    if 1 <= choice <= len(GPU_OPTIONS):
        if choice == len(GPU_OPTIONS):  # "Other" option
            gpu_type = click.prompt("Enter GPU type")
        else:
            gpu_type = GPU_OPTIONS[choice-1][0].replace(" ", "")  # Remove spaces for API
        console.print(f"\n[green]Selected:[/green] {gpu_type}")
        return gpu_type
    else:
        console.print("[red]Invalid choice[/red]")
        return None


@click.command()
@click.option('--name', '-n', required=True, help='Instance name')
@click.option('--gpus', '-g', default=1, help='Number of GPUs (0 for CPU-only)')
@click.option('--gpu-type', '-t', help='GPU type (e.g., A100, H100, RTX5090)')
@click.option('--cpu', '-c', type=int, help='Minimum CPU cores (for CPU-only mode)')
@click.option('--ram', '-r', type=int, help='Minimum RAM in GB (for CPU-only mode)')
@click.option('--disk', '-d', default=100, help='Disk space in GB')
@click.option('--min-bandwidth', '-mbw', type=int, help='Minimum bandwidth in Mbps')
@click.option('--max-price', type=float, help='Maximum price per hour')
@click.option('--project', '-p', help='Project name for organization')
@click.option('--template', '-T', help='Provisioning template (e.g., ml-training, minimal)')
@click.option('--image', '-i', help='Docker image override')
@click.option('--env-file', '-e', help='Path to .env file for environment variables')
@click.option('--wait-timeout', default=600, help='Timeout for instance startup (seconds)')
@click.option('--fast', is_flag=True, help='Fast mode: skip heavy installs (torch, etc.)')
@pass_ctx
def start(ctx, name, gpus, gpu_type, cpu, ram, disk, min_bandwidth, max_price, project, template, image, env_file, wait_timeout, fast):
    """Start a new GPU instance or resume a stopped one

    Examples:
        vastctl start -n ml -g 8 -t A100 -mbw 1000      # 8x A100 GPUs
        vastctl start -n train -g 0 -c 64 -r 128        # CPU-only: 64 cores, 128GB RAM
        vastctl start -n quick -t A100 --fast           # Fast mode: Jupyter only
        vastctl start -n dev --template ml-training     # Use provisioning template
    """
    if not ctx.config.api_key:
        console.print("[red]Error: Vast.ai API key not found.[/red]")
        console.print("Set the key with environment variable VAST_API_KEY or add api_key to ~/.config/vastctl/config.yaml")
        sys.exit(1)

    # Ensure SSH key exists (auto-generate if missing)
    if not ensure_ssh_key_exists(ctx.config):
        console.print("[red]Error: Could not find or create SSH key[/red]")
        console.print(f"Create one manually: ssh-keygen -t ed25519 -f {ctx.config.ssh_key_path}")
        sys.exit(1)

    # Use context manager for proper httpx client cleanup
    with ctx.get_api() as api:
        _start_with_api(ctx, api, name, gpus, gpu_type, cpu, ram, disk, min_bandwidth, max_price, project, template, image, env_file, wait_timeout, fast)


def _start_with_api(ctx, api, name, gpus, gpu_type, cpu, ram, disk, min_bandwidth, max_price, project, template, image, env_file, wait_timeout, fast):
    """Internal start implementation with API instance."""
    # Check if instance already exists
    existing_instance = ctx.registry.get(name)
    if existing_instance:
        # If it's stopped, try to resume it
        if existing_instance.status == 'stopped':
            console.print(f"[yellow]Instance '{name}' exists but is stopped. Attempting to restart...[/yellow]")

            # Check if the instance still exists on Vast.ai
            vast_instances = api.show_instances()
            vast_instance = None
            for inst in vast_instances:
                if inst.get('id') == existing_instance.vast_id or inst.get('label') == name:
                    vast_instance = inst
                    break

            if vast_instance:
                # Instance exists on Vast.ai, try to start it
                with console.status(f"Starting instance '{name}'..."):
                    try:
                        api.start_instance(vast_instance['id'])
                        # Wait for it to be ready
                        ready_info = api.wait_for_instance(vast_instance['id'], timeout=wait_timeout)

                        # Attach SSH key for reliable SSH access
                        pub_path = ctx.config.ssh_public_key_path
                        if pub_path.exists():
                            ssh_pub = pub_path.read_text().strip()
                            if ssh_pub:
                                try:
                                    api.attach_ssh_key(vast_instance['id'], ssh_pub)
                                except Exception as e:
                                    console.print(f"[yellow]Warning: Could not attach SSH key: {e}[/yellow]")

                        ssh_host, ssh_port = api.get_ssh_info(vast_instance['id'])

                        existing_instance.ssh_host = ssh_host
                        existing_instance.ssh_port = ssh_port
                        existing_instance.vast_id = vast_instance['id']
                        existing_instance.update_status('running')
                        existing_instance.mark_accessed()
                        ctx.registry.update(name, {
                            'ssh_host': ssh_host,
                            'ssh_port': ssh_port,
                            'vast_id': vast_instance['id'],
                            'status': 'running',
                            'last_accessed': existing_instance.last_accessed
                        })

                        console.print(f"[green]✓[/green] Instance '{name}' resumed successfully")
                        console.print(f"  SSH: ssh -p {ssh_port} root@{ssh_host}")
                        console.print("\n[yellow]Note:[/yellow] Jupyter may need 1-2 minutes to restart.")
                        console.print("Use 'vastctl connect' to open Jupyter once ready.")

                        # Auto-sync with cloud after successful resume
                        if ctx.config.cloud_sync_on('start'):
                            ctx.try_cloud_event_sync( 'start', instance_name=name, result='success',
                                                details={'resumed': True})
                        return
                    except Exception as e:
                        console.print(f"[red]Error starting instance: {e}[/red]")
                        # Report error to cloud
                        if ctx.config.cloud_sync_on('start'):
                            ctx.try_cloud_event_sync( 'start', instance_name=name, result='error',
                                                details={'resumed': True, 'error': str(e)})
            else:
                console.print(f"[yellow]Instance '{name}' no longer exists on Vast.ai. Creating new instance...[/yellow]")
                # Remove the old instance record
                ctx.registry.remove(name)
        else:
            console.print(f"[red]Error: Instance '{name}' already exists and is {existing_instance.status}[/red]")
            sys.exit(1)

    # CPU-only mode (gpus=0)
    is_cpu_only = (gpus == 0)

    # Resolve template: CLI arg > config default > None
    effective_template = template or ctx.config.get('default_template')

    # Build effective provisioning from template (early, so we can use template image)
    effective_prov = None
    template_image = None
    if effective_template:
        try:
            store = ProfileStore(ctx.config)
            effective_prov = store.build_effective_provisioning(effective_template)
            template_image = store.get_profile_image(effective_template)
            desc = store.get_profile_description(effective_template)
            console.print(f"[dim]Using template: {effective_template}" + (f" ({desc})" if desc else "") + "[/dim]")
        except KeyError as e:
            console.print(f"[red]Error: {e}[/red]")
            console.print("[dim]Available templates: " + ", ".join(ProfileStore(ctx.config).list_profiles() or ["none"]) + "[/dim]")
            sys.exit(1)

    # Determine effective image (CLI --image > template image > config default)
    if is_cpu_only:
        default_image = "ubuntu:22.04"
    else:
        default_image = ctx.config.get('default_image')
    effective_image = image or template_image or default_image

    if is_cpu_only:
        # CPU-only instance - require cpu and ram options
        if not cpu:
            cpu = click.prompt("Minimum CPU cores", type=int, default=8)
        if not ram:
            ram = click.prompt("Minimum RAM (GB)", type=int, default=32)

        gpu_type = "CPU"  # For display purposes

        # Create instance object for CPU-only
        instance = Instance(
            name=name,
            gpu_type="CPU",
            gpu_count=0,
            disk_gb=disk,
            project=project,
            image=effective_image,
        )

        with console.status(f"Searching for CPU offers ({cpu}+ cores, {ram}GB+ RAM)..."):
            offers = api.search_cpu_offers(
                min_cpus=cpu,
                min_ram_gb=ram,
                max_price=max_price,
                disk_gb=disk
            )

            if not offers:
                console.print(f"[red]Error: No offers found with {cpu}+ CPUs and {ram}GB+ RAM[/red]")
                console.print("\n[yellow]Try:[/yellow]")
                console.print(f"  • Less CPU cores (--cpu {cpu//2})")
                console.print(f"  • Less RAM (--ram {ram//2})")
                console.print("  • Higher max price (--max-price)")
                sys.exit(1)
    else:
        # GPU mode - original logic
        # If no GPU type specified, show selection menu
        if not gpu_type:
            gpu_type = prompt_gpu_type()
            if not gpu_type:
                sys.exit(1)

        # Create instance object
        instance = Instance(
            name=name,
            gpu_type=gpu_type,
            gpu_count=gpus,
            disk_gb=disk,
            project=project,
            image=effective_image,
        )

        # Use default min bandwidth if not provided
        if min_bandwidth is None:
            min_bandwidth = ctx.config.get('defaults.bandwidth_min')

        with console.status(f"Searching for {gpus}x{gpu_type} offers..."):
            offers = api.search_offers(
                gpu_type=gpu_type,
                num_gpus=gpus,
                min_bandwidth=min_bandwidth,
                max_price=max_price,
                disk_gb=disk
            )

            if not offers:
                console.print(f"[red]Error: No offers found for {gpus}x{gpu_type}[/red]")
                console.print("\n[yellow]Try:[/yellow]")
                console.print("  • Different GPU type (use --gpu-type or omit for selection)")
                console.print("  • Higher max price (--max-price)")
                console.print("  • Lower bandwidth requirements (--min-bandwidth)")
                console.print("  • Less disk space (--disk)")
                sys.exit(1)

        # Sort by composite score: balance price, bandwidth, and reliability
        def offer_score(x):
            price = x.get('dph_total', float('inf'))
            bandwidth = x.get('inet_down', 0)
            reliability = x.get('reliability', 0)

            bandwidth_bonus = min(bandwidth / 1000, 0.3)
            reliability_bonus = reliability * 0.2
            effective_price = price * (1 - bandwidth_bonus - reliability_bonus)

            return (effective_price, -bandwidth)

        offers.sort(key=offer_score)
        best_offer = offers[0]

        console.print(f"\n[green]Found {len(offers)} offers![/green]")
        console.print(f"[bold]Best offer:[/bold] ${best_offer.get('dph_total', 0):.2f}/hr")
        console.print(f"  • Location: {best_offer.get('geolocation', 'Unknown')}")
        console.print(f"  • Bandwidth: {best_offer.get('inet_down', 0):.0f} Mbps down / {best_offer.get('inet_up', 0):.0f} Mbps up")
        console.print(f"  • Reliability: {best_offer.get('reliability', 0)*100:.1f}%")
        console.print(f"  • Machine ID: {best_offer.get('machine_id', 'Unknown')}")

    with console.status(f"Creating instance '{name}'..."):
        # Generate Jupyter token
        jupyter_token = ctx.connection.generate_jupyter_token()
        instance.jupyter_token = jupyter_token
        instance.jupyter_port = 8888

        # Prepare optional env file content for SSH injection (NOT sent to Vast API)
        # SECURITY: Secrets are injected via SSH after instance is ready
        final_env_path = None
        env_file_content = ""
        if env_file:
            final_env_path = Path(env_file)
        elif (Path.cwd() / ".vastenv").exists():
            final_env_path = Path.cwd() / ".vastenv"
            console.print(f"[dim]Using local env file: {final_env_path}[/dim]")
        elif ctx.config.default_env_path.exists():
            final_env_path = ctx.config.default_env_path
            console.print(f"[dim]Using global env file: {final_env_path}[/dim]")

        if final_env_path and final_env_path.exists():
            try:
                env_lines = []
                with open(final_env_path, 'r') as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped and not stripped.startswith('#'):
                            env_lines.append(stripped)
                if env_lines:
                    env_file_content = "\n".join(env_lines)
                    console.print(f"[dim]Will inject {len(env_lines)} env variable(s) via SSH[/dim]")
            except Exception as e:
                console.print(f"[yellow]Warning: Failed to process env file: {e}[/yellow]")

        # Auto-detect credentials from local environment (for SSH injection)
        auto_env_vars = {}
        if not env_file_content:
            auto_env_vars = scrape_credential_env_vars()
            if auto_env_vars:
                console.print(f"[dim]Will inject {len(auto_env_vars)} auto-detected credential(s) via SSH: {', '.join(sorted(auto_env_vars.keys()))}[/dim]")
        else:
            console.print(f"[dim]Using env file for credentials (skipping auto-detect)[/dim]")

        # Build onstart command using provisioning module
        # SECURITY: No secrets in onstart - they're injected via SSH later
        workspace_cmd = ctx.connection.get_storage_workspace_cmd()

        if fast:
            console.print("[dim]Fast mode: skipping heavy installs (torch, transformers)[/dim]")

        onstart_cmd = build_onstart_script(
            ctx.config,
            jupyter_token=jupyter_token,
            provisioning=effective_prov,
            env_setup_cmd="",  # SECURITY: Secrets injected via SSH, not in Vast API payload
            auto_env_cmd="",   # SECURITY: Secrets injected via SSH, not in Vast API payload
            workspace_cmd=workspace_cmd,
            is_cpu_only=is_cpu_only,
            gpu_type=gpu_type,
            fast=fast,
        )

        if template:
            # TODO: Add template setup
            pass

        # Try to create instance, with retry on different offers if one fails
        max_retries = min(3, len(offers))
        result = None
        selected_offer = None

        for attempt in range(max_retries):
            try_offer = offers[attempt]
            try:
                if attempt > 0:
                    console.print(f"[yellow]Retrying with offer #{attempt + 1}: ${try_offer.get('dph_total', 0):.2f}/hr (Machine {try_offer.get('machine_id', 'Unknown')})[/yellow]")

                result = api.create_instance(
                    offer_id=try_offer['id'],
                    disk_gb=disk,
                    image=effective_image,
                    onstart_cmd=onstart_cmd,
                    label=name
                )
                selected_offer = try_offer
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    console.print(f"[yellow]Offer {try_offer.get('machine_id', 'Unknown')} unavailable, trying next...[/yellow]")
                else:
                    raise

        if not result or not selected_offer:
            raise Exception("Failed to create instance after trying multiple offers")

        instance.vast_id = result.get('new_contract')
        instance.price_per_hour = selected_offer.get('dph_total', 0)
        instance.bandwidth_mbps = selected_offer.get('inet_down', 0)
        instance.update_status('starting')
        ctx.registry.add(instance)

    # Wait for instance to be ready
    with console.status(f"Waiting for instance to be ready..."):
        try:
            ready_info = api.wait_for_instance(instance.vast_id, timeout=wait_timeout)

            # Get SSH info first
            ssh_host, ssh_port = api.get_ssh_info(instance.vast_id)
            instance.ssh_host = ssh_host
            instance.ssh_port = ssh_port
            instance.update_status('running')
            instance.mark_accessed()
            # Save the full instance to preserve started_at for cost tracking
            ctx.registry.add(instance)

            # Attach SSH key with retries (Vast.ai timing workaround)
            pub_path = ctx.config.ssh_public_key_path
            if pub_path.exists():
                ssh_pub = pub_path.read_text().strip()
                if ssh_pub:
                    console.print("[dim]Attaching SSH key...[/dim]")
                    if not ensure_ssh_key_attached(api, instance.vast_id, ssh_pub, retries=5, delay=3):
                        console.print("[yellow]Warning: Could not verify SSH key attachment[/yellow]")

            # Wait for SSH to be actually ready before proceeding
            console.print("[dim]Waiting for SSH to be ready...[/dim]")
            if not wait_for_ssh_ready(ctx.config, instance, timeout=120):
                console.print("[yellow]Warning: SSH not responding yet, continuing anyway...[/yellow]")
                console.print(f"[dim]If SSH fails, try: vastctl stop {name} && vastctl start -n {name}[/dim]")

            # Setup workspace with retry (SSH may not be ready immediately)
            console.print("[dim]Setting up workspace...[/dim]")
            for attempt in range(3):
                try:
                    time.sleep(5)  # Wait for SSH to be ready
                    ctx.storage.setup_workspace(instance)
                    break
                except Exception as e:
                    if attempt < 2:
                        console.print(f"[dim]Waiting for SSH... (attempt {attempt + 1}/3)[/dim]")
                        time.sleep(10)
                    else:
                        console.print(f"[yellow]Warning: Workspace setup failed: {e}[/yellow]")
                        console.print("[dim]You can manually setup later with 'vastctl cp'[/dim]")

            # SECURITY: Inject secrets via SSH (never sent to Vast API)
            # Retry injection since SSH may still be settling (Vast.ai timing issue)
            if env_file_content:
                console.print("[dim]Injecting environment variables via SSH...[/dim]")
                env_injected = False
                for attempt in range(5):
                    if ctx.connection.inject_env_file(instance, env_file_content):
                        env_injected = True
                        break
                    if attempt < 4:
                        console.print(f"[dim]SSH not ready, retrying... ({attempt + 1}/5)[/dim]")
                        time.sleep(5)
                if not env_injected:
                    console.print("[yellow]Warning: Failed to inject env file[/yellow]")

            if auto_env_vars:
                console.print("[dim]Injecting auto-detected credentials via SSH...[/dim]")
                auto_injected = False
                for attempt in range(5):
                    if ctx.connection.inject_auto_env(instance, auto_env_vars):
                        auto_injected = True
                        break
                    if attempt < 4:
                        time.sleep(5)
                if not auto_injected:
                    console.print("[yellow]Warning: Failed to inject auto-env[/yellow]")

        except TimeoutError:
            status_info = api.get_instance(instance.vast_id)
            status_str = status_info.get("actual_status") if status_info else "unknown"
            console.print(f"[red]Error: Instance failed to start in time (last status: {status_str})[/red]")
            instance.update_status('error')
            ctx.registry.update(name, {'status': 'error'})
            sys.exit(1)

    console.print(f"[green]✓[/green] Instance '{name}' is starting")
    console.print(f"  GPUs: {gpus}x {gpu_type}")
    console.print(f"  Disk: {disk} GB")
    if min_bandwidth:
        console.print(f"  Min bandwidth: {min_bandwidth} Mbps")

    console.print("\n[yellow]Note:[/yellow] Jupyter Lab will take 1-2 minutes to start after the instance is ready.")
    console.print("Use 'vastctl connect' to open Jupyter once the instance is running.")

    # Auto-sync with cloud after successful start
    if ctx.config.cloud_sync_on('start'):
        ctx.try_cloud_event_sync( 'start', instance_name=name, result='success',
                            details={'gpu_type': gpu_type, 'gpu_count': gpus})


@click.command()
@click.argument('name', required=False)
@click.option('--all', '-a', is_flag=True, help='Stop all instances')
@click.option('--project', '-p', help='Stop all instances in project')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation')
@pass_ctx
def stop(ctx, name, all, project, yes):
    """Stop GPU instance(s)"""
    instances_to_stop = []

    if all:
        instances_to_stop = ctx.registry.list(status='running')
    elif project:
        instances_to_stop = ctx.registry.list(project=project, status='running')
    elif name:
        instance = ctx.registry.get(name)
        if instance and instance.is_running:
            instances_to_stop = [instance]
        elif not instance:
            console.print(f"[red]Error: Instance '{name}' not found[/red]")
            sys.exit(1)
    else:
        # Stop active instance
        instance = ctx.registry.get_active()
        if instance and instance.is_running:
            instances_to_stop = [instance]
        else:
            console.print("[red]Error: No active instance to stop[/red]")
            sys.exit(1)

    if not instances_to_stop:
        console.print("[yellow]No running instances to stop[/yellow]")
        return

    # Confirm
    if not yes and ctx.config.get('ui.confirm_stop'):
        names = [i.name for i in instances_to_stop]
        if not click.confirm(f"Stop {len(names)} instance(s): {', '.join(names)}?"):
            return

    # Stop instances
    stopped_names = []
    failed_names = []
    with ctx.get_api() as api:
        for instance in instances_to_stop:
            with console.status(f"Stopping '{instance.name}'..."):
                if instance.vast_id:
                    try:
                        if ctx.config.verify_mutations:
                            api.stop_instance_verified(
                                instance.vast_id,
                                timeout=180,
                                poll_s=ctx.config.vast_poll_interval_seconds
                            )
                        else:
                            api.stop_instance(instance.vast_id)
                        instance.update_status('stopped')
                        stopped_names.append(instance.name)
                    except Exception as e:
                        console.print(f"[red]Warning: Failed to stop instance via API: {e}[/red]")
                        instance.update_status('error')
                        failed_names.append(instance.name)
                else:
                    instance.update_status('stopped')
                    stopped_names.append(instance.name)

                ctx.registry.add(instance)

            console.print(f"[green]✓[/green] Stopped '{instance.name}'")
            if ctx.config.get('ui.show_costs'):
                cost = humanize.intcomma(instance.current_cost)
                console.print(f"  Total cost: ${cost}")

    # Auto-sync with cloud after stop (with event details)
    if ctx.config.cloud_sync_on('stop'):
        result = 'success' if not failed_names else ('partial' if stopped_names else 'error')
        ctx.try_cloud_event_sync( 'stop', result=result,
                            details={'stopped': stopped_names, 'failed': failed_names})


@click.command()
@click.argument('name')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation')
@click.option('--force', '-f', is_flag=True, help='Force kill even if instance is running')
@pass_ctx
def kill(ctx, name, yes, force):
    """Destroy instance on Vast.ai and remove from registry"""
    instance = ctx.registry.get(name)
    if not instance:
        console.print(f"[red]Error: Instance '{name}' not found[/red]")
        sys.exit(1)

    # Show instance details
    console.print(f"\n[bold]Instance details:[/bold]")
    console.print(f"  Name: {instance.name}")
    console.print(f"  Vast ID: {instance.vast_id}")
    console.print(f"  Status: {instance.status}")
    console.print(f"  GPUs: {instance.gpu_count}x{instance.gpu_type}")
    console.print(f"  Total cost: ${instance.current_cost:.2f}")

    # Warning for running instances
    if instance.is_running and not force:
        console.print(f"\n[red]Error: Instance '{name}' is still running![/red]")
        console.print("Use --force to kill a running instance, or stop it first.")
        sys.exit(1)

    # Confirm destruction
    if not yes:
        console.print(f"\n[bold red]WARNING:[/bold red] This will permanently destroy the instance!")
        console.print("All data on the instance will be lost.")
        if not click.confirm(f"Destroy instance '{name}' on Vast.ai?"):
            return

    # Kill on Vast.ai with verified destroy for parity
    if instance.vast_id:
        with ctx.get_api() as api:
            with console.status(f"Destroying instance on Vast.ai..."):
                try:
                    if ctx.config.verify_mutations:
                        api.destroy_instance_verified(
                            instance.vast_id,
                            timeout=300,
                            poll_s=ctx.config.vast_poll_interval_seconds
                        )
                    else:
                        api.destroy_instance(instance.vast_id)

                    ctx.registry.remove(name)
                    console.print(f"[green]✓[/green] Instance destroyed on Vast.ai")
                    console.print(f"[green]✓[/green] Instance '{name}' removed from registry")
                    console.print(f"\n[green]Instance '{name}' has been completely destroyed[/green]")

                    if ctx.config.cloud_sync_on('kill'):
                        ctx.try_cloud_event_sync( 'kill', instance_name=name, result='success')

                except TimeoutError as e:
                    console.print(f"[yellow]Warning: Destroy request sent but verification timed out[/yellow]")
                    console.print(f"[yellow]{e}[/yellow]")
                    console.print("[yellow]Registry entry kept for reconciliation.[/yellow]")
                    console.print("Use 'vastctl refresh' to sync state, then 'vastctl remove' if needed.")

                    if ctx.config.cloud_sync_on('kill'):
                        ctx.try_cloud_event_sync( 'kill', instance_name=name, result='timeout')

                except Exception as e:
                    console.print(f"[red]Error destroying instance: {e}[/red]")
                    console.print("[yellow]Registry entry kept for reconciliation.[/yellow]")

                    if ctx.config.cloud_sync_on('kill'):
                        ctx.try_cloud_event_sync( 'kill', instance_name=name, result='error')
    else:
        # No vast_id - check if instance exists on Vast by name/label before removing
        console.print(f"\n[yellow]Warning: No Vast ID stored for '{name}'[/yellow]")
        console.print("[yellow]Checking Vast.ai for instances with this name...[/yellow]")

        found_on_vast = False
        with ctx.get_api() as api:
            try:
                vast_instances = api.show_instances()
                for vi in vast_instances:
                    if vi.get('label') == name:
                        found_on_vast = True
                        console.print(f"\n[red]CRITICAL: Found instance on Vast.ai![/red]")
                        console.print(f"  Vast ID: {vi.get('id')}")
                        console.print(f"  Status: {vi.get('actual_status')}")
                        console.print(f"  Cost/hr: ${vi.get('dph_total', 0):.2f}")

                        if click.confirm(f"\nDestroy this instance on Vast.ai?"):
                            with console.status("Destroying instance on Vast.ai..."):
                                if ctx.config.verify_mutations:
                                    api.destroy_instance_verified(vi['id'], timeout=300)
                                else:
                                    api.destroy_instance(vi['id'])
                            console.print(f"[green]✓[/green] Instance destroyed on Vast.ai")
                        else:
                            console.print("[red]Instance NOT destroyed on Vast.ai - you are still being charged![/red]")
                            console.print(f"[yellow]To destroy manually: vast destroy instance {vi.get('id')}[/yellow]")
                            return
                        break
            except Exception as e:
                console.print(f"[yellow]Could not verify on Vast.ai: {e}[/yellow]")

        if not found_on_vast:
            console.print("[dim]No matching instance found on Vast.ai[/dim]")

        ctx.registry.remove(name)
        console.print(f"[green]✓[/green] Instance '{name}' removed from registry")

        if ctx.config.cloud_sync_on('kill'):
            ctx.try_cloud_event_sync( 'kill', instance_name=name, result='success',
                                 details={'registry_only': not found_on_vast})


@click.command(name="list")
@click.option('--all', '-a', is_flag=True, help='Show all instances including stopped')
@click.option('--project', '-p', help='Filter by project')
@pass_ctx
def list_cmd(ctx, all, project):
    """List instances"""
    if all:
        instances = ctx.registry.list()
    elif project:
        instances = ctx.registry.list(project=project)
    else:
        instances = ctx.registry.list(status='running')

    if not instances:
        console.print("[yellow]No instances found[/yellow]")
        if not all:
            console.print("[dim]Use --all to see stopped instances[/dim]")
        return

    # Backfill started_at for running instances missing it (for cost tracking)
    api = None
    for inst in instances:
        if inst.is_running and not inst.started_at and inst.vast_id:
            try:
                if api is None:
                    api = VastAPI(ctx.config.api_key)
                vast_info = api.get_instance(inst.vast_id)
                start_date = vast_info.get('start_date')
                if start_date:
                    from datetime import datetime
                    inst.started_at = datetime.fromtimestamp(start_date)
                    ctx.registry.add(inst)
            except Exception:
                pass  # Silently skip if backfill fails

    table = Table(title="Instances")
    table.add_column("Name", style="cyan")
    table.add_column("Status")
    table.add_column("GPUs")
    table.add_column("$/hr")
    table.add_column("Cost")
    table.add_column("Project")

    for inst in instances:
        status_style = "green" if inst.is_running else "yellow" if inst.status == "stopped" else "red"
        table.add_row(
            inst.name,
            f"[{status_style}]{inst.status}[/{status_style}]",
            f"{inst.gpu_count}x{inst.gpu_type}",
            f"${inst.price_per_hour:.2f}" if inst.price_per_hour else "-",
            f"${inst.current_cost:.2f}",
            inst.project or "-"
        )

    console.print(table)


@click.command()
@click.argument('name', required=False)
@click.option('--all', '-a', is_flag=True, help='Show all instances')
@pass_ctx
def status(ctx, name, all):
    """Show instance status"""
    if all:
        instances = ctx.registry.list()
    elif name:
        instance = ctx.registry.get(name)
        if instance:
            instances = [instance]
        else:
            console.print(f"[red]Error: Instance '{name}' not found[/red]")
            sys.exit(1)
    else:
        instance = ctx.registry.get_active()
        if instance:
            instances = [instance]
        else:
            console.print("[yellow]No active instance[/yellow]")
            console.print("[dim]Use 'vastctl start' to create one or 'vastctl use <name>' to select one[/dim]")
            return

    for inst in instances:
        status_color = "green" if inst.is_running else "yellow" if inst.status == "stopped" else "red"
        console.print(f"\n[bold]{inst.name}[/bold] [{status_color}]{inst.status}[/{status_color}]")
        console.print(f"  GPUs: {inst.gpu_count}x {inst.gpu_type}")
        console.print(f"  Price: ${inst.price_per_hour:.2f}/hr" if inst.price_per_hour else "  Price: -")
        console.print(f"  Total Cost: ${inst.current_cost:.2f}")
        if inst.ssh_host and inst.ssh_port:
            console.print(f"  SSH: ssh -p {inst.ssh_port} root@{inst.ssh_host}")
        if inst.project:
            console.print(f"  Project: {inst.project}")


@click.command()
@click.argument('name')
@pass_ctx
def use(ctx, name):
    """Set active instance"""
    instance = ctx.registry.get(name)
    if not instance:
        console.print(f"[red]Error: Instance '{name}' not found[/red]")
        sys.exit(1)

    ctx.registry.set_active(name)
    console.print(f"[green]✓[/green] Active instance set to '{name}'")


@click.command()
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation for cleanup')
@click.option('--project', '-p', help='Only refresh instances in project')
@click.option('--verify-setup', is_flag=True, help='Verify SSH and Jupyter status')
@pass_ctx
def refresh(ctx, yes, project, verify_setup):
    """Refresh instance statuses from Vast.ai"""
    with ctx.get_api() as api:
        with console.status("Fetching instances from Vast.ai..."):
            vast_instances = api.show_instances()

        console.print(f"[dim]Found {len(vast_instances)} instance(s) on Vast.ai[/dim]")

        # Update registry with remote state
        local_instances = ctx.registry.list(project=project) if project else ctx.registry.list()

        for inst in local_instances:
            # Find matching remote instance
            remote = None
            for r in vast_instances:
                if r.get('id') == inst.vast_id or r.get('label') == inst.name:
                    remote = r
                    break

            if remote:
                actual_status = remote.get('actual_status', 'unknown')
                if actual_status == 'running':
                    inst.update_status('running')
                elif actual_status == 'exited':
                    inst.update_status('stopped')
                else:
                    inst.update_status(actual_status)

                ctx.registry.update(inst.name, {'status': inst.status})
                console.print(f"  {inst.name}: {inst.status}")
            else:
                console.print(f"  [yellow]{inst.name}: not found on Vast.ai[/yellow]")


@click.command()
@click.argument('name', required=False)
@click.option('--restart', '-r', is_flag=True, help='Restart Jupyter if not running')
@click.option('--force-restart', '-f', is_flag=True, help='Force restart Jupyter even if running')
@click.option('--port', '-p', default=8888, help='Local port for Jupyter (default: 8888)')
@click.option('--show-token', is_flag=True, help='Print the Jupyter token separately (security risk)')
@pass_ctx
def connect(ctx, name, restart, force_restart, port, show_token):
    """Connect to instance (open Jupyter)"""
    # Get instance
    if name:
        instance = ctx.registry.get(name)
    else:
        instance = ctx.registry.get_active()

    if not instance:
        console.print("[red]Error: No instance specified or active[/red]")
        sys.exit(1)

    if not instance.is_running:
        console.print(f"[red]Error: Instance '{instance.name}' is not running[/red]")
        sys.exit(1)

    # Check if Jupyter is running
    console.print(f"[dim]Checking Jupyter status on '{instance.name}'...[/dim]")
    jupyter_running = ctx.connection.check_jupyter_running(instance)

    # Handle restart options
    should_restart = False
    if force_restart:
        should_restart = True
        console.print("[yellow]Force restarting Jupyter...[/yellow]")
    elif not jupyter_running:
        if restart:
            should_restart = True
            console.print("[yellow]Jupyter not running, restarting...[/yellow]")
        else:
            console.print("[yellow]Jupyter is not running on the instance[/yellow]")
            if click.confirm("Would you like to restart Jupyter?"):
                should_restart = True

    # Restart if needed
    if should_restart:
        token = instance.jupyter_token
        if not token:
            token = ctx.connection.generate_jupyter_token()

        success = ctx.connection.restart_jupyter(instance, token=token, port=8888)

        if success:
            ctx.registry.update(instance.name, {
                "jupyter_token": token,
                "jupyter_port": 8888
            })
            instance.jupyter_token = token

            console.print(f"[green]✓[/green] Jupyter restarted successfully")
            console.print("[dim]Waiting for Jupyter to be ready...[/dim]")
            time.sleep(10)
        else:
            console.print("[red]Failed to restart Jupyter[/red]")
            console.print(f"[dim]Check logs with: vastctl ssh {instance.name} 'cat /tmp/jupyter.log'[/dim]")
            sys.exit(1)

    # Open Jupyter connection
    success = ctx.connection.open_jupyter(instance, port=port)
    if success:
        console.print(f"[green]✓[/green] Jupyter opened for '{instance.name}'")
        console.print(f"[dim]Tunnel established on localhost:{port}[/dim]")

        token = instance.jupyter_token or ""
        url = f"http://localhost:{port}/lab?token={token}"
        console.print(f"\n[bold]Jupyter URL:[/bold] {url}")
        if show_token:
            console.print(f"[bold]Token:[/bold] {token}")

        # Try to copy to clipboard
        try:
            import sys as _sys
            if _sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=url.encode(), check=True)
                console.print("\n[dim]URL copied to clipboard![/dim]")
        except:
            pass
    else:
        console.print(f"[red]Error: Failed to open Jupyter[/red]")
        console.print("\n[yellow]Troubleshooting tips:[/yellow]")
        console.print("1. Try: vastctl connect --restart")
        console.print(f"2. Check logs: vastctl ssh {instance.name} 'cat /tmp/jupyter.log'")
        console.print(f"3. Manually restart: vastctl restart-jupyter {instance.name}")


@click.command()
@click.argument('name', required=False)
@click.option('--token', help='Custom Jupyter token (default: generates random)')
@click.option('--port', default=8888, help='Jupyter port (default: 8888)')
@click.option('--show-token', is_flag=True, help='Print the Jupyter token (security risk)')
@pass_ctx
def restart_jupyter(ctx, name, token, port, show_token):
    """Restart Jupyter Lab on an instance"""
    if name:
        instance = ctx.registry.get(name)
    else:
        instance = ctx.registry.get_active()

    if not instance:
        console.print("[red]Error: No instance specified or active[/red]")
        sys.exit(1)

    if not instance.is_running:
        console.print(f"[red]Error: Instance '{instance.name}' is not running[/red]")
        sys.exit(1)

    console.print(f"Restarting Jupyter on '{instance.name}'...")

    if not token:
        token = ctx.connection.generate_jupyter_token()

    success = ctx.connection.restart_jupyter(instance, token=token, port=port)

    if success:
        ctx.registry.update(instance.name, {
            "jupyter_token": token,
            "jupyter_port": port
        })

        console.print(f"\n[green]✓[/green] Jupyter restarted successfully!")
        console.print(f"[bold]Port:[/bold] {port}")
        if show_token:
            console.print(f"[bold]Token:[/bold] {token}")
        console.print(f"\n[dim]Wait 10-15 seconds for Jupyter to fully start[/dim]")
        console.print(f"[dim]Then run: vastctl connect {instance.name}[/dim]")
    else:
        console.print(f"[red]Error: Failed to restart Jupyter[/red]")
        console.print(f"[dim]Try connecting via SSH and checking /tmp/jupyter.log[/dim]")


@click.command()
@click.argument('name', required=False)
@click.option('--test', '-t', is_flag=True, help='Test SSH connection without connecting')
@click.option('--tmux', is_flag=True, help='Connect via tmux session (attach or create)')
@click.option('--tmux-new', is_flag=True, help='Create a new tmux window')
@pass_ctx
def ssh(ctx, name, test, tmux, tmux_new):
    """SSH into instance

    By default, opens a plain SSH shell.

    Examples:
        vastctl ssh jet              # Plain SSH
        vastctl ssh jet --tmux       # Attach to or create tmux session
        vastctl ssh jet --tmux-new   # Create new tmux window
    """
    if name:
        instance = ctx.registry.get(name)
    else:
        instance = ctx.registry.get_active()

    if not instance:
        console.print("[red]Error: No instance specified or active[/red]")
        sys.exit(1)

    if not instance.is_running:
        console.print(f"[red]Error: Instance '{instance.name}' is not running[/red]")
        sys.exit(1)

    if test:
        # Just test the connection
        console.print(f"[dim]Testing SSH connection to {instance.name}...[/dim]")
        if ctx.connection.test_connection(instance):
            console.print(f"[green]✓[/green] SSH connection successful")
        else:
            print_ssh_diagnostic(instance.name, config=ctx.config)
            sys.exit(1)
        return

    # Quick pre-flight check - try a simple SSH command first
    ssh_key = ctx.config.ssh_key_path
    if not ssh_key.exists():
        console.print(f"[red]Error: SSH key not found at {ssh_key}[/red]")
        console.print("Generate one with: ssh-keygen -t ed25519")
        sys.exit(1)

    # Test connection before exec'ing into SSH
    # (exec replaces process, so we can't catch errors after)
    test_cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-i", str(ssh_key),
        "-p", str(instance.ssh_port),
        f"root@{instance.ssh_host}",
        "echo ok"
    ]

    result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=15)

    if result.returncode != 0:
        if "Permission denied" in result.stderr:
            print_ssh_diagnostic(instance.name, config=ctx.config)
        else:
            console.print(f"[red]SSH connection failed:[/red] {result.stderr.strip()}")
        sys.exit(1)

    # Connection works, now exec into interactive SSH
    ctx.connection.ssh_connect(instance, tmux=tmux, tmux_new=tmux_new)


@click.command()
@click.argument('cmd_args', nargs=-1)
@click.option('--name', '-n', help='Instance name')
@click.option('--stream', '-s', is_flag=True, help='Stream output in real-time')
@click.option('--cd', help='Change to directory before running')
@click.option('--env', '-e', multiple=True, help='Set environment variable (VAR=value)')
@pass_ctx
def run(ctx, cmd_args, name, stream, cd, env):
    """Run command on instance"""
    if name:
        instance = ctx.registry.get(name)
    else:
        instance = ctx.registry.get_active()

    if not instance:
        console.print("[red]Error: No instance specified or active[/red]")
        sys.exit(1)

    if not instance.is_running:
        console.print(f"[red]Error: Instance '{instance.name}' is not running[/red]")
        sys.exit(1)

    if not cmd_args:
        console.print("[red]Error: No command specified[/red]")
        sys.exit(1)

    # Build command
    cmd = " ".join(cmd_args)

    # Add env vars
    if env:
        env_exports = " ".join([f"export {e};" for e in env])
        cmd = f"{env_exports} {cmd}"

    # Add cd
    if cd:
        cmd = f"cd {cd} && {cmd}"

    # Execute
    output, error = ctx.connection.execute_remote_command(instance, cmd)

    if output:
        console.print(output)
    if error:
        console.print(f"[red]{error}[/red]")


@click.command()
@click.argument('name')
@click.option('--all', '-a', 'delete_all', is_flag=True, help='Also delete all backups')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation')
@pass_ctx
def remove(ctx, name, delete_all, yes):
    """Remove an instance record from VastCtl registry (does not touch remote)"""
    instance = ctx.registry.get(name)
    if not instance:
        console.print(f"[red]Error: Instance '{name}' not found in registry[/red]")
        sys.exit(1)

    prompt = f"Remove instance '{name}' from VastCtl registry?"
    if delete_all:
        prompt += " This will also delete all backups!"

    if instance.is_running and not yes:
        if not click.confirm(f"Instance '{name}' appears running. Remove record anyway? (remote will not be stopped)"):
            return
    elif not yes and not click.confirm(prompt):
        return

    ctx.registry.remove(name)
    console.print(f"[green]✓[/green] Instance '{name}' removed from registry")


@click.command()
@click.option('--gpu-type', '-t', help='GPU type to search for')
@click.option('--gpus', '-g', default=1, help='Number of GPUs')
@click.option('--min-bandwidth', '-mbw', type=int, help='Minimum bandwidth in Mbps')
@click.option('--max-price', type=float, help='Maximum price per hour')
@click.option('--disk', '-d', default=100, help='Disk space in GB')
@click.option('--limit', '-l', default=10, help='Maximum results to show')
@pass_ctx
def search(ctx, gpu_type, gpus, min_bandwidth, max_price, disk, limit):
    """Search for GPU offers"""
    if not ctx.config.api_key:
        console.print("[red]Error: Vast.ai API key not found[/red]")
        sys.exit(1)

    # Resolve GPU type: CLI arg > config default > prompt
    if not gpu_type:
        gpu_type = ctx.config.get('default_gpu_type')

    if not gpu_type:
        gpu_type = prompt_gpu_type()
        if not gpu_type:
            console.print("[red]Error: GPU type is required[/red]")
            sys.exit(1)

    with ctx.get_api() as api:
        with console.status(f"Searching for {gpus}x {gpu_type} offers..."):
            offers = api.search_offers(
                gpu_type=gpu_type,
                num_gpus=gpus,
                min_bandwidth=min_bandwidth,
                max_price=max_price,
                disk_gb=disk
            )

        if not offers:
            console.print("[yellow]No offers found matching criteria[/yellow]")
            return

        # Show results
        table = Table(title=f"Found {len(offers)} offers")
        table.add_column("GPU", style="cyan")
        table.add_column("$/hr")
        table.add_column("BW (Mbps)")
        table.add_column("Reliability")
        table.add_column("Location")
        table.add_column("Machine ID")

        for offer in offers[:limit]:
            table.add_row(
                f"{offer.get('num_gpus', 1)}x {offer.get('gpu_name', 'Unknown')}",
                f"${offer.get('dph_total', 0):.2f}",
                f"{offer.get('inet_down', 0):.0f}",
                f"{offer.get('reliability', 0)*100:.0f}%",
                offer.get('geolocation', 'Unknown'),
                str(offer.get('machine_id', 'Unknown'))
            )

        console.print(table)


@click.command()
@click.option('--cpus', '-c', type=int, help='Minimum CPU cores')
@click.option('--ram', '-r', type=int, help='Minimum RAM in GB')
@click.option('--max-price', type=float, help='Maximum price per hour')
@click.option('--disk', '-d', default=100, help='Disk space in GB')
@click.option('--limit', '-l', default=10, help='Maximum results to show')
@pass_ctx
def search_cpu(ctx, cpus, ram, max_price, disk, limit):
    """Search for CPU-only offers"""
    if not ctx.config.api_key:
        console.print("[red]Error: Vast.ai API key not found[/red]")
        sys.exit(1)

    with ctx.get_api() as api:
        with console.status(f"Searching for CPU offers..."):
            offers = api.search_cpu_offers(
                min_cpus=cpus,
                min_ram_gb=ram,
                max_price=max_price,
                disk_gb=disk
            )

        if not offers:
            console.print("[yellow]No CPU offers found matching criteria[/yellow]")
            return

        table = Table(title=f"Found {len(offers)} CPU offers")
        table.add_column("CPUs")
        table.add_column("RAM")
        table.add_column("$/hr")
        table.add_column("Location")

        for offer in offers[:limit]:
            table.add_row(
                str(offer.get('cpu_cores_effective', 'Unknown')),
                f"{offer.get('cpu_ram', 0)/1024:.0f} GB",
                f"${offer.get('dph_total', 0):.4f}",
                offer.get('geolocation', 'Unknown')
            )

        console.print(table)
