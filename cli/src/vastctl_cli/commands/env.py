"""Environment management commands for VastLab CLI."""

import sys
import json

import click
from rich.console import Console
from rich.table import Table

from vastctl_core.environment import EnvironmentManager
from vastctl_core.auto_env import scrape_credential_env_vars

from ..context import CliContext

console = Console()
pass_obj = click.pass_obj


@click.group('env', invoke_without_command=True)
@click.argument('instance_name', required=False)
@click.pass_context
def env_group(click_ctx, instance_name):
    """Environment detection and automated setup"""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(env_detect, instance_name=instance_name)


@env_group.command('detect')
@click.argument('instance_name', required=False)
@click.option('--format', '-f', 'fmt', type=click.Choice(['table', 'json']), default='table', help='Output format')
@pass_obj
def env_detect(ctx: CliContext, instance_name, fmt):
    """Detect environment characteristics of an instance"""
    # Get instance
    if instance_name:
        instance = ctx.registry.get(instance_name)
    else:
        instance = ctx.registry.get_active()

    if not instance:
        console.print("[red]Error: No instance specified or active[/red]")
        sys.exit(1)

    if not instance.is_running:
        console.print(f"[red]Error: Instance '{instance.name}' is not running[/red]")
        sys.exit(1)

    env_manager = EnvironmentManager(ctx.config, ctx.connection)

    with console.status(f"Detecting environment on '{instance.name}'..."):
        env_info = env_manager.detect_environment(instance)

    if fmt == 'json':
        console.print(json.dumps(env_info, indent=2))
    else:
        console.print(f"\n[bold]Environment: {instance.name}[/bold]\n")

        table = Table()
        table.add_column("Property", style="cyan")
        table.add_column("Value")

        for key, value in env_info.items():
            if isinstance(value, dict):
                table.add_row(key, json.dumps(value))
            else:
                table.add_row(key, str(value))

        console.print(table)


@env_group.command('local')
@click.option('--format', '-f', 'fmt', type=click.Choice(['table', 'json']), default='table', help='Output format')
def env_local(fmt):
    """Show auto-detected local credentials"""
    env_vars = scrape_credential_env_vars()

    if fmt == 'json':
        # Redact values for security
        redacted = {k: f"{v[:4]}...{v[-4:]}" if len(v) > 8 else "***" for k, v in env_vars.items()}
        console.print(json.dumps(redacted, indent=2))
    else:
        if not env_vars:
            console.print("[yellow]No credential environment variables detected[/yellow]")
            console.print("[dim]Set AWS_*, WANDB_*, HF_*, OPENAI_*, etc. in your shell[/dim]")
            return

        console.print(f"[bold]Detected Credentials ({len(env_vars)} variables)[/bold]\n")

        table = Table()
        table.add_column("Variable", style="cyan")
        table.add_column("Value (redacted)")

        for key, value in sorted(env_vars.items()):
            # Redact value for security
            if len(value) > 8:
                redacted = f"{value[:4]}...{value[-4:]}"
            else:
                redacted = "***"
            table.add_row(key, redacted)

        console.print(table)
        console.print("\n[dim]These will be auto-injected when starting new instances[/dim]")


@env_group.command('inject')
@click.argument('instance_name', required=False)
@click.option('--env-file', '-e', help='Path to .env file')
@click.option('--auto', '-a', is_flag=True, help='Inject auto-detected credentials')
@pass_obj
def env_inject(ctx: CliContext, instance_name, env_file, auto):
    """Manually inject environment variables into a running instance"""
    # Get instance
    if instance_name:
        instance = ctx.registry.get(instance_name)
    else:
        instance = ctx.registry.get_active()

    if not instance:
        console.print("[red]Error: No instance specified or active[/red]")
        sys.exit(1)

    if not instance.is_running:
        console.print(f"[red]Error: Instance '{instance.name}' is not running[/red]")
        sys.exit(1)

    injected = False

    if env_file:
        from pathlib import Path
        env_path = Path(env_file)
        if not env_path.exists():
            console.print(f"[red]Error: File '{env_file}' not found[/red]")
            sys.exit(1)

        # Read and inject
        env_content = env_path.read_text()
        with console.status("Injecting environment file..."):
            if ctx.connection.inject_env_file(instance, env_content):
                console.print(f"[green]✓[/green] Injected env file to '{instance.name}'")
                injected = True
            else:
                console.print("[red]Failed to inject env file[/red]")

    if auto:
        env_vars = scrape_credential_env_vars()
        if not env_vars:
            console.print("[yellow]No credentials detected in local environment[/yellow]")
        else:
            with console.status(f"Injecting {len(env_vars)} credential(s)..."):
                if ctx.connection.inject_auto_env(instance, env_vars):
                    console.print(f"[green]✓[/green] Injected {len(env_vars)} auto-detected credential(s)")
                    injected = True
                else:
                    console.print("[red]Failed to inject auto-detected credentials[/red]")

    if not env_file and not auto:
        console.print("[yellow]Specify --env-file or --auto to inject variables[/yellow]")
