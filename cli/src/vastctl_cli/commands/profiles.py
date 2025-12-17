"""Profile management commands for VastLab CLI."""

import sys
import json
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table

from vastctl_core.profiles import ProfileStore

from ..context import CliContext

console = Console()
pass_obj = click.pass_obj


@click.group('profiles', invoke_without_command=True)
@click.pass_context
def profiles_group(click_ctx):
    """Manage provisioning profiles"""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(profiles_list)


@profiles_group.command('list')
@pass_obj
def profiles_list(ctx: CliContext):
    """List available provisioning profiles"""
    store = ProfileStore(ctx.config)
    profile_names = store.list_profiles()

    if not profile_names:
        console.print("[yellow]No profiles found.[/yellow]")
        console.print("[dim]Define profiles in config.yaml or pull from cloud with 'vastctl profiles pull'[/dim]")
        return

    console.print("[bold]Available Provisioning Profiles[/bold]\n")

    table = Table()
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="dim")
    table.add_column("Description")

    for name in profile_names:
        profile = store.get_profile(name)
        desc = profile.get("description", "") if profile else ""

        # Determine source (check new location first, then legacy)
        new_loc = ctx.config.get("provisioning_profiles", {}) or {}
        legacy = ctx.config.get("profiles", {}) or {}
        if name in new_loc and isinstance(new_loc[name], dict):
            source = "local"
        elif name in legacy and isinstance(legacy[name], dict):
            source = "local"
        else:
            source = "cloud cache"

        table.add_row(name, source, desc)

    console.print(table)
    console.print(f"\n[dim]Use 'vastctl profiles show <name>' for details[/dim]")


@profiles_group.command('show')
@click.argument('name')
@click.option('--format', '-f', 'fmt', type=click.Choice(['yaml', 'json']), default='yaml', help='Output format')
@pass_obj
def profiles_show(ctx: CliContext, name, fmt):
    """Show details of a provisioning profile"""
    import yaml

    store = ProfileStore(ctx.config)
    profile = store.get_profile(name)

    if not profile:
        console.print(f"[red]Profile '{name}' not found[/red]")
        console.print("[dim]Available profiles: " + ", ".join(store.list_profiles() or ["none"]) + "[/dim]")
        return

    console.print(f"[bold]Profile: {name}[/bold]\n")

    if fmt == 'json':
        console.print(json.dumps(profile, indent=2))
    else:
        console.print(yaml.dump(profile, default_flow_style=False, sort_keys=False))


@profiles_group.command('pull')
@click.option('--force', '-f', is_flag=True, help='Overwrite existing cache')
@pass_obj
def profiles_pull(ctx: CliContext, force):
    """Pull provisioning profiles from cloud to local cache"""
    # Check if cloud is enabled
    if not ctx.config.cloud_enabled:
        console.print("[yellow]Cloud features are disabled in config.[/yellow]")
        console.print("[dim]Enable with: vastctl config set cloud.enabled true[/dim]")
        return

    store = ProfileStore(ctx.config)
    cache_path = ctx.config.profiles_cache_path

    # Check existing cache
    if cache_path.exists() and not force:
        try:
            existing = json.loads(cache_path.read_text())
            if existing.get('profiles'):
                count = len(existing['profiles'])
                ts = existing.get('ts', 'unknown')
                console.print(f"[yellow]Cache already has {count} profile(s) (last updated: {ts})[/yellow]")
                console.print("[dim]Use --force to overwrite[/dim]")
                return
        except Exception:
            pass  # Proceed with pull if cache is corrupted

    with console.status("Pulling profiles from cloud..."):
        try:
            with ctx.get_cloud() as cloud:
                if not cloud.is_enabled:
                    console.print("[yellow]Cloud is not properly configured.[/yellow]")
                    console.print("[dim]Run 'vastctl login' to authenticate[/dim]")
                    return

                # List profiles from cloud
                profile_list = cloud.list_profiles()

                if not profile_list:
                    console.print("[yellow]No profiles available from cloud.[/yellow]")
                    return

                # Fetch full details for each profile
                profiles = {}
                for p in profile_list:
                    name = p.get('name') or p.get('slug')
                    if name:
                        try:
                            full_profile = cloud.get_profile(name)
                            profiles[name] = full_profile
                        except Exception as e:
                            console.print(f"[yellow]Warning: Could not fetch profile '{name}': {e}[/yellow]")

                # Save to cache
                cache_data = {
                    'profiles': profiles,
                    'ts': datetime.now(timezone.utc).isoformat(),
                }
                store.save_cloud_cache(cache_data)

                console.print(f"[green]✓[/green] Pulled {len(profiles)} profile(s) from cloud")
                for name in sorted(profiles.keys()):
                    desc = profiles[name].get('description', '')
                    console.print(f"  • {name}" + (f" - {desc}" if desc else ""))

        except Exception as e:
            console.print(f"[red]Error pulling profiles: {e}[/red]")
