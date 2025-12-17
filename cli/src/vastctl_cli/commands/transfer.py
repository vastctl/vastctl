"""File transfer commands for VastLab CLI."""

import sys
from pathlib import Path
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table
import humanize

from vastctl_core import Config, Registry, Instance
from ..context import CliContext

console = Console()
pass_ctx = click.make_pass_decorator(CliContext, ensure=True)


def parse_remote_path(path: str, registry):
    """Parse remote path in format 'instance:path' or ':path'.

    Returns (instance, remote_path) or (None, None) if not a remote path.
    """
    if ':' not in path:
        return None, None

    # Handle ':path' format (uses active instance)
    if path.startswith(':'):
        inst = registry.get_active()
        return inst, path[1:]

    # Handle 'instance:path' format
    parts = path.split(':', 1)
    if len(parts) == 2:
        instance_name, remote_path = parts
        inst = registry.get(instance_name)
        if inst:
            return inst, remote_path

    return None, None


@click.command()
@click.argument('source')
@click.argument('destination')
@click.option('--recursive', '-r', is_flag=True, help='Copy directories recursively')
@click.option('--force-include', is_flag=True, help='Include files even if in .gitignore')
@click.option('--max-size', type=int, help='Maximum file size in MB to include')
@click.option('--instance', '-i', help='Instance name (default: from path or active)')
@click.option('--parallel', '-p', is_flag=True, help='Use parallel transfers')
@click.option('--workers', '-w', default=4, help='Number of parallel workers')
@click.option('--limit', type=int, help='Limit number of files to transfer')
@pass_ctx
def cp(ctx, source, destination, recursive, force_include, max_size, instance, parallel, workers, limit):
    """Copy files to/from instance

    Examples:
        vastctl cp -r ./dir jet:/remote/         # Upload to 'jet' instance
        vastctl cp jet:/remote/file.txt ./       # Download from 'jet'
        vastctl cp ./file.txt :/remote/path      # Upload to active instance
        vastctl cp -r :remote/dir ./local/       # Download from active instance
    """
    # Parse source and destination for remote paths
    src_inst, src_remote = parse_remote_path(source, ctx.registry)
    dst_inst, dst_remote = parse_remote_path(destination, ctx.registry)

    # Determine direction and instance
    is_upload = src_inst is None and dst_inst is not None
    is_download = src_inst is not None and dst_inst is None

    if is_upload:
        inst = dst_inst
        local_path = Path(source)
        remote_path = dst_remote
    elif is_download:
        inst = src_inst
        remote_path = src_remote
        local_path = Path(destination)
    else:
        console.print("[red]Error: Use 'instance:path' or ':path' for remote paths[/red]")
        console.print("[dim]Examples: vastctl cp ./local jet:/remote  or  vastctl cp jet:/remote ./local[/dim]")
        sys.exit(1)

    # Override instance if explicitly specified
    if instance:
        inst = ctx.registry.get(instance)

    if not inst:
        console.print("[red]Error: No instance specified or active[/red]")
        sys.exit(1)

    if not inst.is_running:
        console.print(f"[red]Error: Instance '{inst.name}' is not running[/red]")
        sys.exit(1)

    # Execute transfer
    if is_upload:

        if not local_path.exists():
            console.print(f"[red]Error: Local path '{source}' not found[/red]")
            sys.exit(1)

        with console.status(f"Uploading to {inst.name}..."):
            if recursive or local_path.is_dir():
                # Use recursive copy for directories
                result = ctx.storage.copy_recursive_to_instance(
                    inst, str(local_path), remote_path,
                    force_include=force_include,
                    max_size_mb=max_size
                )
                success = result.get("success", False)
                if success:
                    files_copied = len(result.get("files_copied", []))
                    files_skipped = len(result.get("files_skipped", []))
                    total_mb = result.get("total_size_mb", 0)
                    console.print(f"[green]✓[/green] Uploaded {files_copied} files ({total_mb:.1f}MB) to {inst.name}:{remote_path}")
                    if files_skipped:
                        console.print(f"[yellow]  Skipped {files_skipped} files (use --force-include to include)[/yellow]")
                else:
                    console.print(f"[red]Upload failed: {result.get('error', 'Unknown error')}[/red]")
                    sys.exit(1)
            else:
                # Single file copy
                success = ctx.storage.copy_to_instance(inst, str(local_path), remote_path)
                if success:
                    console.print(f"[green]✓[/green] Uploaded to {inst.name}:{remote_path}")
                else:
                    console.print("[red]Upload failed[/red]")
                    sys.exit(1)
    else:
        with console.status(f"Downloading from {inst.name}..."):
            if recursive:
                # For recursive download, use scp -r
                import subprocess
                scp_cmd = [
                    "scp", "-r",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "LogLevel=ERROR",
                    "-i", str(ctx.config.ssh_key_path),
                    "-P", str(inst.ssh_port),
                    f"root@{inst.ssh_host}:{remote_path}",
                    str(local_path)
                ]
                result = subprocess.run(scp_cmd, capture_output=True, text=True)
                success = result.returncode == 0
            else:
                success = ctx.storage.copy_from_instance(inst, remote_path, str(local_path))

        if success:
            console.print(f"[green]✓[/green] Downloaded to {local_path}")
        else:
            console.print("[red]Download failed[/red]")
            sys.exit(1)


@click.command()
@click.argument('name', required=False)
@click.option('--patterns', '-p', multiple=True, help='File patterns to backup')
@click.option('--exclude', '-e', multiple=True, help='Patterns to exclude')
@pass_ctx
def backup(ctx, name, patterns, exclude):
    """Backup instance workspace to local storage"""
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

    with console.status(f"Creating backup of '{instance.name}'..."):
        backup_path = ctx.storage.backup_instance(
            instance,
            patterns=list(patterns) if patterns else None,
            exclude_patterns=list(exclude) if exclude else None
        )

    if backup_path:
        console.print(f"[green]✓[/green] Backup created: {backup_path}")
    else:
        console.print("[red]Backup failed[/red]")
        sys.exit(1)


@click.command()
@click.argument('name', required=False)
@click.option('--backup-file', '-b', help='Specific backup file to restore')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation')
@pass_ctx
def restore(ctx, name, backup_file, yes):
    """Restore instance workspace from backup"""
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

    # Find backup to restore
    backup_path = None
    if backup_file:
        backup_path = Path(backup_file)
    else:
        backup_list = ctx.storage.list_backups(instance.name)
        if not backup_list:
            console.print(f"[yellow]No backups found for '{instance.name}'[/yellow]")
            return
        # Most recent backup (list is sorted by created desc)
        backup_path = backup_list[0]['path']
        backup_file = backup_path.name

    if not yes:
        if not click.confirm(f"Restore backup '{backup_file}' to '{instance.name}'?"):
            return

    with console.status(f"Restoring backup to '{instance.name}'..."):
        success = ctx.storage.restore_instance(instance, backup_path)

    if success:
        console.print(f"[green]✓[/green] Backup restored to '{instance.name}'")
    else:
        console.print("[red]Restore failed[/red]")
        sys.exit(1)


@click.command()
@click.argument('name', required=False)
@pass_ctx
def backups(ctx, name):
    """List backups for an instance"""
    if name:
        instance = ctx.registry.get(name)
    else:
        instance = ctx.registry.get_active()

    if not instance:
        console.print("[red]Error: No instance specified or active[/red]")
        sys.exit(1)

    backup_list = ctx.storage.list_backups(instance.name)

    if not backup_list:
        console.print(f"[yellow]No backups found for '{instance.name}'[/yellow]")
        return

    table = Table(title=f"Backups for '{instance.name}'")
    table.add_column("File", style="cyan")
    table.add_column("Size")
    table.add_column("Created")

    for backup_info in backup_list:
        path = backup_info.get('path')
        size = humanize.naturalsize(backup_info.get('size', 0))
        created = backup_info.get('created')
        if created:
            created_str = created.strftime("%Y-%m-%d %H:%M")
        else:
            created_str = "-"
        table.add_row(path.name if path else "-", size, created_str)

    console.print(table)


@click.command(name="sync-files")
@click.argument('source')
@click.argument('target')
@click.option('--patterns', '-p', multiple=True, help='File patterns to sync')
@click.option('--yes', '-y', is_flag=True, help='Skip confirmation')
@pass_ctx
def sync_files(ctx, source, target, patterns, yes):
    """Sync files between local and remote"""
    console.print("[yellow]File sync not yet implemented[/yellow]")
    console.print("[dim]Use 'vastctl cp' for now[/dim]")


@click.command()
@click.argument('name', required=False)
@click.option('--copy', '-c', is_flag=True, help='Copy SFTP URL to clipboard')
@click.option('--open', '-o', 'open_client', is_flag=True, help='Open in FileZilla (if installed)')
@pass_ctx
def sftp(ctx, name, copy, open_client):
    """Get SFTP connection details for FileZilla/WinSCP

    Examples:
        vastctl sftp jet              # Show connection details
        vastctl sftp jet -c           # Copy SFTP URL to clipboard
        vastctl sftp jet -o           # Open in FileZilla
    """
    import subprocess
    import shutil

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

    if not instance.ssh_host or not instance.ssh_port:
        console.print(f"[red]Error: No SSH connection info for '{instance.name}'[/red]")
        sys.exit(1)

    # Get SSH key path
    key_path = ctx.config.ssh_key_path

    # Build SFTP URL (FileZilla format)
    sftp_url = f"sftp://root@{instance.ssh_host}:{instance.ssh_port}"

    # Display connection details
    console.print(f"\n[bold]SFTP Connection for '{instance.name}'[/bold]\n")
    console.print(f"  [cyan]Protocol:[/cyan]  SFTP")
    console.print(f"  [cyan]Host:[/cyan]      {instance.ssh_host}")
    console.print(f"  [cyan]Port:[/cyan]      {instance.ssh_port}")
    console.print(f"  [cyan]User:[/cyan]      root")
    console.print(f"  [cyan]Key:[/cyan]       {key_path}")
    console.print(f"\n  [cyan]URL:[/cyan]       {sftp_url}")

    # FileZilla Site Manager XML snippet
    console.print(f"\n[dim]FileZilla Site Manager settings:[/dim]")
    console.print(f"  Protocol: SFTP - SSH File Transfer Protocol")
    console.print(f"  Host: {instance.ssh_host}")
    console.print(f"  Port: {instance.ssh_port}")
    console.print(f"  Logon Type: Key file")
    console.print(f"  User: root")
    console.print(f"  Key file: {key_path}")

    # Copy to clipboard
    if copy:
        try:
            # Try xclip (Linux), pbcopy (Mac), or clip (Windows)
            if shutil.which('xclip'):
                subprocess.run(['xclip', '-selection', 'clipboard'], input=sftp_url.encode(), check=True)
            elif shutil.which('pbcopy'):
                subprocess.run(['pbcopy'], input=sftp_url.encode(), check=True)
            elif shutil.which('clip'):
                subprocess.run(['clip'], input=sftp_url.encode(), check=True, shell=True)
            else:
                console.print("\n[yellow]Could not copy to clipboard - no clipboard tool found[/yellow]")
                return
            console.print(f"\n[green]✓[/green] SFTP URL copied to clipboard")
        except Exception as e:
            console.print(f"\n[yellow]Could not copy to clipboard: {e}[/yellow]")

    # Open in FileZilla
    if open_client:
        filezilla_path = shutil.which('filezilla')
        if filezilla_path:
            try:
                # FileZilla accepts sftp:// URLs directly
                subprocess.Popen([filezilla_path, sftp_url])
                console.print(f"\n[green]✓[/green] Opening in FileZilla...")
            except Exception as e:
                console.print(f"\n[yellow]Could not open FileZilla: {e}[/yellow]")
        else:
            console.print("\n[yellow]FileZilla not found in PATH[/yellow]")
            console.print("[dim]Install with: sudo apt install filezilla[/dim]")
