"""Training command for VastCtl CLI."""

import sys
import subprocess
from pathlib import Path

import click
from rich.console import Console

from vastctl_core.train import TrainJob, TrainExecutor

from ..context import CliContext

console = Console()
pass_ctx = click.make_pass_decorator(CliContext, ensure=True)


@click.command()
@click.argument("script", required=False)
@click.argument("script_args", nargs=-1, type=click.UNPROCESSED)
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path (train.yaml)")
@click.option("--instance", "-n", help="Instance name (default: active instance)")
@click.option("--outputs", "-o", default="/workspace/outputs", help="Remote output directory")
@click.option("--no-upload", is_flag=True, help="Skip uploading local directory")
@click.option("--no-deps", is_flag=True, help="Skip dependency installation")
@click.option("--sync-dir", type=click.Path(exists=True), help="Directory to upload (default: current)")
@click.option("--wandb-project", help="W&B project name")
@click.option("--attach", is_flag=True, help="Attach to tmux session after starting")
@pass_ctx
def train(ctx, script, script_args, config, instance, outputs, no_upload, no_deps, sync_dir, wandb_project, attach):
    """Run a training job on a GPU instance.

    Automates: upload code -> install deps -> run in tmux -> show download command

    \b
    Examples:
        vastctl train train.py --epochs 10 --lr 0.001
        vastctl train train.py -n my-gpu --epochs 10
        vastctl train --config train.yaml
        vastctl train train.py --attach  # Attach to tmux after starting
    """
    # Build job from CLI or config
    if config:
        job = TrainJob.from_config(Path(config))
        # CLI args override config
        if outputs != "/workspace/outputs":
            job.remote_outputs = outputs
        if sync_dir:
            job.sync_dir = Path(sync_dir)
        if wandb_project:
            job.wandb_project = wandb_project
        if no_upload:
            job.no_upload = True
        if no_deps:
            job.no_deps = True
    elif script:
        job = TrainJob.from_cli(
            script=Path(script),
            script_args=list(script_args),
            sync_dir=Path(sync_dir) if sync_dir else Path("."),
            remote_outputs=outputs,
            wandb_project=wandb_project,
            no_upload=no_upload,
            no_deps=no_deps,
        )
    else:
        console.print("[red]Error: Either script or --config required[/red]")
        console.print("[dim]Usage: vastctl train script.py [args...] or vastctl train --config train.yaml[/dim]")
        sys.exit(1)

    # Execute the job
    with console.status("[bold blue]Starting training job...[/bold blue]"):
        executor = TrainExecutor(ctx, job, instance_name=instance)

    try:
        result = executor.run(attach=attach)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

    # Display result
    if result.success:
        _show_started_message(result, job)
    else:
        console.print(f"[red]Training failed: {result.error}[/red]")
        sys.exit(1)


def _show_started_message(result, job: TrainJob):
    """Display message after training starts."""
    console.print()
    console.print("[bold green]Training job started![/bold green]")
    console.print(f"  Instance: [cyan]{result.instance_name}[/cyan]")
    console.print(f"  Script: [cyan]{job.script} {' '.join(job.script_args)}[/cyan]")
    console.print(f"  Tmux session: [cyan]{job.tmux_session}[/cyan]")

    console.print()
    console.print("[bold]Monitor your training:[/bold]")
    console.print(f"  SSH + tmux:  [dim]vastctl ssh {result.instance_name} --tmux[/dim]")
    if job.wandb_project:
        console.print(f"  Wandb:       [dim]https://wandb.ai/{job.wandb_project}[/dim]")

    console.print()
    console.print("[bold]When complete, download artifacts:[/bold]")
    download_cmd = result.download_command
    console.print(f"  [green]{download_cmd}[/green]")

    # Copy to clipboard if possible
    try:
        subprocess.run(["pbcopy"], input=download_cmd.encode(), check=True, capture_output=True)
        console.print("  [dim](copied to clipboard)[/dim]")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
