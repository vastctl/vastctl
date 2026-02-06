"""File transfer commands for VastLab CLI."""

import sys
import subprocess
import re
from pathlib import Path
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TransferSpeedColumn, TimeRemainingColumn
import humanize

from vastctl_core import Config, Registry, Instance
from ..context import CliContext

console = Console()
pass_ctx = click.make_pass_decorator(CliContext, ensure=True)


def get_rsync_version() -> tuple:
    """Get rsync major.minor version as tuple.

    Returns (0, 0) for Apple's openrsync which doesn't support --info=progress2.
    """
    try:
        result = subprocess.run(['rsync', '--version'], capture_output=True, text=True)
        # Apple's openrsync shows "openrsync: protocol version X"
        if 'openrsync' in result.stdout:
            return (0, 0)  # Treat as old version
        # GNU rsync shows "rsync  version X.Y.Z"
        match = re.search(r'rsync\s+version\s+(\d+)\.(\d+)', result.stdout)
        if match:
            return (int(match.group(1)), int(match.group(2)))
    except Exception:
        pass
    return (2, 0)  # Assume old version


def run_rsync_with_progress(cmd: list, description: str, console: Console) -> bool:
    """Run rsync command and display progress using rich.

    Uses --info=progress2 on rsync 3.1+, falls back to --progress on older versions.
    """
    version = get_rsync_version()
    use_modern_progress = version >= (3, 1)

    # Add progress flags if not already present
    if use_modern_progress:
        if '--info=progress2' not in cmd:
            cmd.insert(1, '--info=progress2')
            cmd.insert(2, '--no-inc-recursive')
    else:
        if '--progress' not in cmd:
            cmd.insert(1, '--progress')

    # Choose columns based on whether we have real byte progress
    if use_modern_progress:
        columns = [
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ]
    else:
        # Old rsync: no real byte counts, just show file progress
        columns = [
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.fields[speed]}"),
        ]

    with Progress(*columns, console=console, transient=True) as progress:
        task = progress.add_task(description, total=100, speed="")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        files_done = 0

        for line in process.stdout:
            line = line.strip()

            if use_modern_progress:
                # Modern rsync: "1,234,567  45%  12.34MB/s    0:01:23"
                match = re.search(r'(\d+)%', line)
                if match:
                    pct = int(match.group(1))
                    progress.update(task, completed=pct)
            else:
                # Old rsync --progress: "  1234567 100%   12.34MB/s    0:00:01"
                if '100%' in line:
                    files_done += 1
                    progress.update(task, description=f"{description} ({files_done} files)")

                # Extract speed from rsync output
                speed_match = re.search(r'([\d.]+[KMG]?B/s)', line)
                if speed_match:
                    progress.update(task, speed=speed_match.group(1))

                # Update percentage (per current file)
                pct_match = re.search(r'(\d+)%', line)
                if pct_match:
                    pct = int(pct_match.group(1))
                    progress.update(task, completed=min(pct, 99))

        process.wait()

        if process.returncode == 0:
            progress.update(task, completed=100)
            return True
        else:
            stderr = process.stderr.read()
            if stderr and "No such file" not in stderr:
                console.print(f"[red]Error: {stderr}[/red]")
            return False


def parse_remote_path(path: str, registry):
    """Parse remote path in format 'instance:path' or ':path'.

    Returns (instance, remote_path, error_msg) tuple.
    - If valid: (instance, remote_path, None)
    - If not a remote path: (None, None, None)
    - If instance not found: (None, None, "error message")
    """
    if ':' not in path:
        return None, None, None

    # Handle ':path' format (uses active instance)
    if path.startswith(':'):
        inst = registry.get_active()
        if not inst:
            return None, None, "No active instance set. Use 'vastctl use <name>' or specify instance in path."
        return inst, path[1:], None

    # Handle 'instance:path' format
    parts = path.split(':', 1)
    if len(parts) == 2:
        instance_name, remote_path = parts
        inst = registry.get(instance_name)
        if inst:
            return inst, remote_path, None
        else:
            return None, None, f"Instance '{instance_name}' not found. Run 'vastctl refresh' to import instances from Vast.ai."

    return None, None, None


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
    src_inst, src_remote, src_err = parse_remote_path(source, ctx.registry)
    dst_inst, dst_remote, dst_err = parse_remote_path(destination, ctx.registry)

    # Check for instance lookup errors
    if src_err:
        console.print(f"[red]Error: {src_err}[/red]")
        sys.exit(1)
    if dst_err:
        console.print(f"[red]Error: {dst_err}[/red]")
        sys.exit(1)

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

        if recursive or local_path.is_dir():
            # Use rsync with progress for directory uploads
            # Ensure source path ends with / to copy contents, not create nested dir
            src_path = str(local_path)
            if not src_path.endswith('/'):
                src_path += '/'

            rsync_cmd = [
                "rsync",
                "-avz",
                "-e", f"ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR -i {ctx.config.ssh_key_path} -p {inst.ssh_port}",
                src_path,
                f"root@{inst.ssh_host}:{remote_path}"
            ]

            # Add exclude patterns from config
            for pattern in ctx.config.transfer_exclude_patterns:
                rsync_cmd.insert(2, f"--exclude={pattern}")

            # Add size limit exclusion if configured
            if not force_include and ctx.config.ignore_large_files:
                effective_max = max_size if max_size else ctx.config.max_file_size_mb
                rsync_cmd.insert(2, f"--max-size={effective_max}M")

            success = run_rsync_with_progress(rsync_cmd, f"Uploading to {inst.name}", console)
            if success:
                console.print(f"[green]✓[/green] Uploaded to {inst.name}:{remote_path}")
            else:
                console.print("[red]Upload failed[/red]")
                sys.exit(1)
        else:
            # Single file upload with rsync + progress
            rsync_cmd = [
                "rsync",
                "-avz",
                "-e", f"ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR -i {ctx.config.ssh_key_path} -p {inst.ssh_port}",
                str(local_path),
                f"root@{inst.ssh_host}:{remote_path}"
            ]
            success = run_rsync_with_progress(rsync_cmd, f"Uploading to {inst.name}", console)
            if success:
                console.print(f"[green]✓[/green] Uploaded to {inst.name}:{remote_path}")
            else:
                console.print("[red]Upload failed[/red]")
                sys.exit(1)
    else:
        # Ensure local directory exists
        if recursive:
            local_path.mkdir(parents=True, exist_ok=True)
        elif local_path.is_dir() or str(local_path).endswith('/'):
            local_path.mkdir(parents=True, exist_ok=True)
        else:
            local_path.parent.mkdir(parents=True, exist_ok=True)

        if recursive:
            # Use rsync with progress for recursive downloads
            rsync_cmd = [
                "rsync",
                "-avz",
                "-e", f"ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR -i {ctx.config.ssh_key_path} -p {inst.ssh_port}",
                f"root@{inst.ssh_host}:{remote_path}",
                str(local_path)
            ]
            success = run_rsync_with_progress(rsync_cmd, f"Downloading from {inst.name}", console)
        else:
            # Single file - use rsync with progress
            rsync_cmd = [
                "rsync",
                "-avz",
                "-e", f"ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR -i {ctx.config.ssh_key_path} -p {inst.ssh_port}",
                f"root@{inst.ssh_host}:{remote_path}",
                str(local_path)
            ]
            success = run_rsync_with_progress(rsync_cmd, f"Downloading from {inst.name}", console)

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
