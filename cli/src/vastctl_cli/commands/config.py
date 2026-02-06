"""Configuration management commands for VastLab CLI."""

import sys

import click
from rich.console import Console
import yaml

from ..context import CliContext

console = Console()
pass_obj = click.pass_obj


@click.group('config', invoke_without_command=True)
@click.pass_context
def config_group(click_ctx):
    """Manage VastCtl configuration settings"""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(config_show)


@config_group.command('show')
@click.option('--key', '-k', help='Show specific config key')
@pass_obj
def config_show(ctx: CliContext, key):
    """Show current configuration"""
    if key:
        value = ctx.config.get(key)
        if value is None:
            console.print(f"[yellow]Key '{key}' not found[/yellow]")
        else:
            console.print(f"[bold]{key}:[/bold] {value}")
    else:
        console.print("[bold]VastCtl Configuration[/bold]\n")
        console.print(f"Config file: {ctx.config.config_path}")
        console.print(f"Data directory: {ctx.config.data_dir}\n")

        # Show config as YAML
        config_dict = ctx.config._config.copy()

        # Redact sensitive values
        if 'api_key' in config_dict:
            key_val = config_dict['api_key']
            if key_val:
                config_dict['api_key'] = key_val[:8] + '...' + key_val[-4:] if len(key_val) > 12 else '***'

        console.print(yaml.dump(config_dict, default_flow_style=False, sort_keys=False))


@config_group.command('set')
@click.argument('key')
@click.argument('value')
@pass_obj
def config_set(ctx: CliContext, key, value):
    """Set a configuration value

    Examples:
        vastctl config set api_key sk-xxx
        vastctl config set cloud.enabled true
        vastctl config set defaults.disk_gb 200
    """
    # Parse value type
    if value.lower() == 'true':
        value = True
    elif value.lower() == 'false':
        value = False
    elif value.isdigit():
        value = int(value)
    else:
        try:
            value = float(value)
        except ValueError:
            pass  # Keep as string

    ctx.config.set(key, value)
    # Note: Config.set() already calls save() internally
    console.print(f"[green]✓[/green] Set {key} = {value}")


@config_group.command('get')
@click.argument('key')
@pass_obj
def config_get(ctx: CliContext, key):
    """Get a configuration value"""
    value = ctx.config.get(key)
    if value is None:
        console.print(f"[yellow]Key '{key}' not found[/yellow]")
        sys.exit(1)
    else:
        console.print(value)


@config_group.command('path')
@pass_obj
def config_path(ctx: CliContext):
    """Show configuration file path"""
    console.print(ctx.config.config_path)


@config_group.command('init')
@click.option('--force', '-f', is_flag=True, help='Overwrite existing config')
@pass_obj
def config_init(ctx: CliContext, force):
    """Create config file with defaults

    The config file is normally created on-demand when you use 'config set'
    or 'config edit'. Use this command to explicitly create it with all
    default values.
    """
    if ctx.config.config_path.exists() and not force:
        console.print(f"[yellow]Config file already exists:[/yellow] {ctx.config.config_path}")
        console.print("[dim]Use --force to overwrite with defaults[/dim]")
        return

    ctx.config.save()
    console.print(f"[green]✓[/green] Created config file: {ctx.config.config_path}")


@config_group.command('edit')
@pass_obj
def config_edit(ctx: CliContext):
    """Open configuration file in editor"""
    import os
    import subprocess

    # Ensure config file exists before editing
    if not ctx.config.config_path.exists():
        ctx.config.save()
        console.print(f"[dim]Created config file: {ctx.config.config_path}[/dim]")

    editor = os.environ.get('EDITOR', 'vim')
    try:
        subprocess.run([editor, str(ctx.config.config_path)])
    except FileNotFoundError:
        console.print(f"[red]Editor '{editor}' not found[/red]")
        console.print(f"[dim]Set EDITOR environment variable or edit manually: {ctx.config.config_path}[/dim]")
