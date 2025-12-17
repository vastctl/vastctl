"""Cloud integration commands for VastLab CLI."""

import sys

import click
from rich.console import Console
from rich.table import Table

from vastctl_core.auth import AuthStore, load_token, save_token, delete_token, get_token_source
from vastctl_core.snapshot import build_snapshot

from ..context import CliContext

console = Console()
pass_obj = click.pass_obj


@click.command()
@click.option('--token', '-t', help='Paste your cloud API token')
@pass_obj
def login(ctx: CliContext, token):
    """Authenticate with VastCtl Cloud

    Get your token from the VastCtl Cloud dashboard and paste it here.

    Example:
        vastctl login --token <your-token>
    """
    if not ctx.config.cloud_enabled:
        console.print("[yellow]Cloud features are disabled in config.[/yellow]")
        console.print("[dim]Enable with: vastctl config set cloud.enabled true[/dim]")
        return

    auth_store = AuthStore(token_file=ctx.config.cloud_token_file)

    # Check if already logged in
    existing_token = load_token(auth_store)
    if existing_token and not token:
        console.print("[yellow]Already logged in.[/yellow]")
        if not click.confirm("Re-authenticate?"):
            return

    # Prompt for token if not provided
    if not token:
        console.print("[bold]VastCtl Cloud Login[/bold]\n")
        console.print("Get your token from: [cyan]https://vastctl.cloud/settings/api[/cyan]\n")
        token = click.prompt("Paste your API token", hide_input=True)

    if not token or not token.strip():
        console.print("[red]Error: Token cannot be empty[/red]")
        sys.exit(1)

    token = token.strip()

    # Validate token by making a test request
    with console.status("Validating token..."):
        try:
            save_token(token, auth_store)

            with ctx.get_cloud() as cloud:
                user_info = cloud.verify_token()

            console.print("[green]✓[/green] Successfully logged in!")
            email = user_info.get('email') or user_info.get('user', {}).get('email')
            if email:
                console.print(f"  Welcome, {email}!")

        except Exception as e:
            # Remove invalid token
            delete_token(auth_store)
            console.print(f"[red]Error: Invalid token - {e}[/red]")
            sys.exit(1)


@click.command()
@pass_obj
def logout(ctx: CliContext):
    """Log out from VastCtl Cloud"""
    auth_store = AuthStore(token_file=ctx.config.cloud_token_file)

    if load_token(auth_store):
        delete_token(auth_store)
        console.print("[green]✓[/green] Logged out successfully")
    else:
        console.print("[yellow]Not logged in[/yellow]")


@click.command()
@pass_obj
def whoami(ctx: CliContext):
    """Show current VastCtl Cloud user"""
    if not ctx.config.cloud_enabled:
        console.print("[yellow]Cloud features are disabled[/yellow]")
        return

    auth_store = AuthStore(token_file=ctx.config.cloud_token_file)
    if not load_token(auth_store):
        console.print("[yellow]Not logged in[/yellow]")
        console.print("[dim]Run 'vastctl login' to authenticate[/dim]")
        return

    with ctx.get_cloud() as cloud:
        try:
            user = cloud.verify_token()
            email = user.get('email') or user.get('user', {}).get('email', 'Unknown')
            console.print(f"[bold]Logged in as:[/bold] {email}")

            # Show token source
            source = get_token_source(auth_store)
            if source:
                console.print(f"  Token source: {source}")

            name = user.get('name') or user.get('user', {}).get('name')
            if name:
                console.print(f"  Name: {name}")

            org = user.get('organization') or user.get('org', {}).get('name')
            if org:
                console.print(f"  Organization: {org}")

        except Exception as e:
            console.print(f"[red]Error fetching user info: {e}[/red]")


@click.command(name="sync")
@pass_obj
def sync_cloud(ctx: CliContext):
    """Sync instance state with VastCtl Cloud"""
    if not ctx.config.cloud_enabled:
        console.print("[yellow]Cloud features are disabled[/yellow]")
        console.print("[dim]Enable with: vastctl config set cloud.enabled true[/dim]")
        return

    with console.status("Syncing with cloud..."):
        try:
            with ctx.get_cloud() as cloud:
                if not cloud.is_enabled:
                    console.print("[yellow]Cloud is not properly configured[/yellow]")
                    console.print("[dim]Run 'vastctl login' to authenticate[/dim]")
                    return

                snapshot = build_snapshot(
                    ctx.config.config_dir,
                    ctx.registry.list(),
                )
                cloud.push_snapshot(snapshot)

            console.print("[green]✓[/green] Synced with cloud")
            console.print(f"  Instances: {snapshot['summary']['total_instances']}")
            console.print(f"  Running: {snapshot['summary']['running_instances']}")
        except Exception as e:
            console.print(f"[red]Sync failed: {e}[/red]")
