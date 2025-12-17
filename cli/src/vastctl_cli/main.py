"""Main entry point for VastCtl CLI.

This module provides the CLI interface to VastCtl.
All business logic is in vastctl-core; this package only handles
CLI presentation (click commands, rich output).
"""

import click
from rich.console import Console

from vastctl_core import __version__

from .context import CliContext

console = Console()

# Create pass decorator for CLI context
pass_ctx = click.make_pass_decorator(CliContext, ensure=True)


@click.group()
@click.version_option(version=__version__, prog_name="vastctl")
@click.pass_context
def cli(ctx):
    """VastCtl - GPU instance manager for Vast.ai (third-party)

    Manage GPU instances on Vast.ai with a simple, powerful CLI.

    Examples:
        vastctl start -n ml -g 8 -t A100     # Start 8x A100 instance
        vastctl connect ml                    # Open Jupyter
        vastctl stop ml                       # Stop instance
        vastctl kill ml                       # Destroy instance
    """
    if ctx.obj is None:
        ctx.obj = CliContext.create()


# Import and register command groups
from .commands import instances
from .commands import transfer
from .commands import cloud
from .commands import profiles
from .commands import config
from .commands import env

# Register individual commands from instances module
cli.add_command(instances.start)
cli.add_command(instances.stop)
cli.add_command(instances.kill)
cli.add_command(instances.list_cmd, name="list")
cli.add_command(instances.status)
cli.add_command(instances.use)
cli.add_command(instances.refresh)
cli.add_command(instances.connect)
cli.add_command(instances.restart_jupyter, name="restart-jupyter")
cli.add_command(instances.ssh)
cli.add_command(instances.run)
cli.add_command(instances.remove)
cli.add_command(instances.search)
cli.add_command(instances.search_cpu, name="search-cpu")

# Register transfer commands
cli.add_command(transfer.cp)
cli.add_command(transfer.sftp)
cli.add_command(transfer.backup)
cli.add_command(transfer.restore)
cli.add_command(transfer.backups)
cli.add_command(transfer.sync_files)

# Register cloud commands
cli.add_command(cloud.login)
cli.add_command(cloud.logout)
cli.add_command(cloud.whoami)
cli.add_command(cloud.sync_cloud, name="sync")

# Register command groups
cli.add_command(profiles.profiles_group, name="profiles")
cli.add_command(config.config_group, name="config")
cli.add_command(env.env_group, name="env")


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
