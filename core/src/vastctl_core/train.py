"""Training job management for vastctl."""

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Any, TYPE_CHECKING

import yaml

from .deps import DependencyDetector

if TYPE_CHECKING:
    from .instance import Instance


@dataclass
class TrainConfig:
    """Configuration loaded from train.yaml."""

    script: str
    args: List[str] = field(default_factory=list)
    sync_directory: str = "."
    sync_exclude: List[str] = field(default_factory=list)
    remote_outputs: str = "/workspace/outputs"
    wandb_project: Optional[str] = None

    @classmethod
    def from_file(cls, config_path: Path) -> "TrainConfig":
        """Load config from YAML file."""
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        if "script" not in data:
            raise ValueError("Config file must specify 'script'")

        sync = data.get("sync", {})
        outputs = data.get("outputs", {})
        wandb = data.get("wandb", {})

        return cls(
            script=data["script"],
            args=data.get("args", []),
            sync_directory=sync.get("directory", "."),
            sync_exclude=sync.get("exclude", []),
            remote_outputs=outputs.get("remote", "/workspace/outputs"),
            wandb_project=wandb.get("project"),
        )


@dataclass
class TrainJob:
    """Represents a training job to execute."""

    script: Path
    script_args: List[str] = field(default_factory=list)
    sync_dir: Path = field(default_factory=lambda: Path("."))
    sync_exclude: List[str] = field(default_factory=list)
    remote_outputs: str = "/workspace/outputs"
    wandb_project: Optional[str] = None
    no_upload: bool = False
    no_deps: bool = False
    tmux_session: str = "train"

    @classmethod
    def from_cli(
        cls,
        script: Path,
        script_args: List[str],
        sync_dir: Optional[Path] = None,
        remote_outputs: str = "/workspace/outputs",
        wandb_project: Optional[str] = None,
        no_upload: bool = False,
        no_deps: bool = False,
    ) -> "TrainJob":
        """Create TrainJob from CLI arguments."""
        return cls(
            script=script,
            script_args=script_args,
            sync_dir=sync_dir or Path("."),
            remote_outputs=remote_outputs,
            wandb_project=wandb_project,
            no_upload=no_upload,
            no_deps=no_deps,
        )

    @classmethod
    def from_config(cls, config_path: Path) -> "TrainJob":
        """Create TrainJob from config file."""
        config = TrainConfig.from_file(config_path)
        return cls(
            script=Path(config.script),
            script_args=config.args,
            sync_dir=Path(config.sync_directory),
            sync_exclude=config.sync_exclude,
            remote_outputs=config.remote_outputs,
            wandb_project=config.wandb_project,
        )


@dataclass
class TrainResult:
    """Result of training job execution."""

    success: bool
    instance_name: str = ""
    download_command: str = ""
    error: Optional[str] = None


class TrainExecutor:
    """Execute training jobs on remote instances."""

    def __init__(self, ctx: Any, job: TrainJob, instance_name: Optional[str] = None):
        """Initialize executor.

        Args:
            ctx: CLI context with registry, connection, storage, config
            job: Training job to execute
            instance_name: Instance name (or None to use active)
        """
        self.ctx = ctx
        self.job = job
        self.instance_name = instance_name
        self.instance: Optional["Instance"] = None

    def run(self, attach: bool = False) -> TrainResult:
        """Execute the full training workflow.

        1. Get instance
        2. Upload code (unless --no-upload)
        3. Install deps (unless --no-deps)
        4. Inject wandb credentials
        5. Start training in tmux

        Args:
            attach: Whether to attach to tmux after starting

        Returns:
            TrainResult with success status and download command
        """
        try:
            # Step 1: Get instance
            self._get_instance()

            # Step 2: Upload project files
            if not self.job.no_upload:
                self._upload_project()

            # Step 3: Install dependencies
            if not self.job.no_deps:
                self._install_dependencies()

            # Step 4: Inject wandb credentials
            self._inject_wandb()

            # Step 5: Start training in tmux
            self._start_training()

            # Step 6: Optionally attach
            if attach:
                self._attach_to_tmux()

            return TrainResult(
                success=True,
                instance_name=self.instance.name,
                download_command=self.get_download_command(),
            )

        except Exception as e:
            return TrainResult(
                success=False,
                instance_name=self.instance.name if self.instance else "",
                error=str(e),
            )

    def _get_instance(self):
        """Get instance from registry."""
        if self.instance_name:
            self.instance = self.ctx.registry.get(self.instance_name)
        else:
            self.instance = self.ctx.registry.get_active()

        if not self.instance:
            raise ValueError(
                "No instance specified or active. "
                "Use -n <name> or 'vastctl use <name>' first."
            )

        if not self.instance.is_running:
            raise ValueError(
                f"Instance '{self.instance.name}' is not running. "
                "Start it first with 'vastctl start'."
            )

    def _upload_project(self):
        """Upload project files to instance using rsync."""
        src_path = str(self.job.sync_dir)
        if not src_path.endswith("/"):
            src_path += "/"

        # Remote path is /workspace/{dirname}
        dirname = self.job.sync_dir.name if self.job.sync_dir.name != "." else Path.cwd().name
        remote_path = f"/workspace/{dirname}"

        # Build rsync command
        rsync_cmd = [
            "rsync",
            "-avz",
            "--progress",
            "-e",
            f"ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR "
            f"-i {self.ctx.config.ssh_key_path} -p {self.instance.ssh_port}",
            src_path,
            f"root@{self.instance.ssh_host}:{remote_path}",
        ]

        # Add exclude patterns
        for pattern in self.ctx.config.transfer_exclude_patterns:
            rsync_cmd.insert(2, f"--exclude={pattern}")
        for pattern in self.job.sync_exclude:
            rsync_cmd.insert(2, f"--exclude={pattern}")

        subprocess.run(rsync_cmd, check=True)

    def _install_dependencies(self):
        """Install project dependencies on remote."""
        spec = DependencyDetector.detect(self.job.sync_dir)

        if spec.is_empty():
            return

        install_cmd = spec.install_command()
        if install_cmd:
            # Change to project directory first
            dirname = self.job.sync_dir.name if self.job.sync_dir.name != "." else Path.cwd().name
            full_cmd = f"cd /workspace/{dirname} && {install_cmd}"
            self.ctx.connection.execute_command(self.instance, full_cmd)

    def _inject_wandb(self):
        """Inject wandb credentials from environment."""
        wandb_key = os.environ.get("WANDB_API_KEY")
        if not wandb_key:
            return

        env_vars = {"WANDB_API_KEY": wandb_key}

        if self.job.wandb_project:
            env_vars["WANDB_PROJECT"] = self.job.wandb_project

        self.ctx.connection.inject_auto_env(self.instance, env_vars)

    def _build_training_command(self) -> str:
        """Build the training command string."""
        args_str = " ".join(self.job.script_args)
        return f"python {self.job.script.name} {args_str}".strip()

    def _build_tmux_command(self, train_cmd: str) -> str:
        """Wrap training command in tmux session."""
        dirname = self.job.sync_dir.name if self.job.sync_dir.name != "." else Path.cwd().name
        session = self.job.tmux_session

        # Kill existing session if any, then create new one
        return f"""
tmux has-session -t {session} 2>/dev/null && tmux kill-session -t {session}
tmux new-session -d -s {session} -c /workspace/{dirname} "{train_cmd}"
""".strip()

    def _start_training(self):
        """Start training script in tmux session."""
        train_cmd = self._build_training_command()
        tmux_cmd = self._build_tmux_command(train_cmd)
        self.ctx.connection.execute_command(self.instance, tmux_cmd)

    def _attach_to_tmux(self):
        """Attach to the tmux session (interactive)."""
        self.ctx.connection.ssh_connect(self.instance, tmux=True)

    def get_download_command(self) -> str:
        """Generate command to download training artifacts."""
        return f"vastctl cp -r {self.instance.name}:{self.job.remote_outputs}/ ./checkpoints/"
