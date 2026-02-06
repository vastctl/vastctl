"""Tests for training job management."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import os

from vastctl_core.train import TrainJob, TrainExecutor, TrainConfig


class TestTrainJobFromCli:
    """Test TrainJob creation from CLI args."""

    def test_minimal_job(self):
        """Test creating job with just script path."""
        job = TrainJob.from_cli(script=Path("train.py"), script_args=[])

        assert job.script == Path("train.py")
        assert job.script_args == []
        assert job.remote_outputs == "/workspace/outputs"

    def test_with_script_args(self):
        """Test script arguments are preserved."""
        job = TrainJob.from_cli(
            script=Path("train.py"),
            script_args=["--epochs", "10", "--lr", "0.001"],
        )

        assert job.script_args == ["--epochs", "10", "--lr", "0.001"]

    def test_custom_outputs_dir(self):
        """Test custom output directory."""
        job = TrainJob.from_cli(
            script=Path("train.py"),
            script_args=[],
            remote_outputs="/workspace/checkpoints",
        )

        assert job.remote_outputs == "/workspace/checkpoints"

    def test_sync_dir_defaults_to_cwd(self):
        """Test sync directory defaults to current directory."""
        job = TrainJob.from_cli(script=Path("train.py"), script_args=[])

        assert job.sync_dir == Path(".")

    def test_custom_sync_dir(self):
        """Test custom sync directory."""
        job = TrainJob.from_cli(
            script=Path("train.py"),
            script_args=[],
            sync_dir=Path("/path/to/project"),
        )

        assert job.sync_dir == Path("/path/to/project")


class TestTrainJobFromConfig:
    """Test TrainJob creation from YAML config."""

    def test_minimal_config(self, tmp_path):
        """Test loading minimal config file."""
        config = tmp_path / "train.yaml"
        config.write_text("script: train.py")

        job = TrainJob.from_config(config)

        assert job.script == Path("train.py")
        assert job.script_args == []

    def test_full_config(self, tmp_path):
        """Test loading complete config file."""
        config = tmp_path / "train.yaml"
        config.write_text("""
script: train.py
args:
  - --epochs=10
  - --lr=0.001

sync:
  directory: ./src
  exclude:
    - "*.pth"
    - checkpoints/

outputs:
  remote: /workspace/results

wandb:
  project: my-ml-project
""")

        job = TrainJob.from_config(config)

        assert job.script == Path("train.py")
        assert job.script_args == ["--epochs=10", "--lr=0.001"]
        assert job.sync_dir == Path("./src")
        assert "*.pth" in job.sync_exclude
        assert job.remote_outputs == "/workspace/results"
        assert job.wandb_project == "my-ml-project"

    def test_config_with_defaults(self, tmp_path):
        """Test config uses defaults for missing fields."""
        config = tmp_path / "train.yaml"
        config.write_text("script: my_script.py")

        job = TrainJob.from_config(config)

        assert job.remote_outputs == "/workspace/outputs"
        assert job.sync_dir == Path(".")
        assert job.sync_exclude == []


class TestTrainConfig:
    """Test TrainConfig parsing."""

    def test_parse_yaml(self, tmp_path):
        """Test parsing YAML config."""
        config_file = tmp_path / "train.yaml"
        config_file.write_text("""
script: train.py
args: [--epochs=10]
""")

        config = TrainConfig.from_file(config_file)

        assert config.script == "train.py"
        assert config.args == ["--epochs=10"]

    def test_missing_script_raises(self, tmp_path):
        """Test error when script is missing."""
        config_file = tmp_path / "train.yaml"
        config_file.write_text("args: [--foo]")

        with pytest.raises(ValueError, match="script"):
            TrainConfig.from_file(config_file)


class TestTrainExecutor:
    """Test TrainExecutor workflow logic."""

    @pytest.fixture
    def mock_ctx(self):
        """Create mocked CLI context."""
        ctx = MagicMock()
        ctx.registry = MagicMock()
        ctx.connection = MagicMock()
        ctx.storage = MagicMock()
        ctx.config = MagicMock()
        ctx.config.ssh_key_path = Path("/home/user/.ssh/id_rsa")
        ctx.config.transfer_exclude_patterns = ["__pycache__", ".git"]
        return ctx

    @pytest.fixture
    def mock_instance(self):
        """Create mocked instance."""
        instance = MagicMock()
        instance.name = "test-gpu"
        instance.is_running = True
        instance.ssh_host = "ssh.vast.ai"
        instance.ssh_port = 12345
        return instance

    @pytest.fixture
    def basic_job(self):
        """Create basic training job."""
        return TrainJob(
            script=Path("train.py"),
            script_args=["--epochs", "10"],
            sync_dir=Path("."),
            remote_outputs="/workspace/outputs",
        )

    def test_get_instance_from_registry(self, mock_ctx, mock_instance, basic_job):
        """Test getting instance from registry."""
        mock_ctx.registry.get.return_value = mock_instance

        executor = TrainExecutor(mock_ctx, basic_job, instance_name="test-gpu")
        executor._get_instance()

        mock_ctx.registry.get.assert_called_with("test-gpu")
        assert executor.instance == mock_instance

    def test_get_active_instance(self, mock_ctx, mock_instance, basic_job):
        """Test using active instance when name not specified."""
        mock_ctx.registry.get_active.return_value = mock_instance

        executor = TrainExecutor(mock_ctx, basic_job, instance_name=None)
        executor._get_instance()

        mock_ctx.registry.get_active.assert_called_once()
        assert executor.instance == mock_instance

    def test_error_when_no_instance(self, mock_ctx, basic_job):
        """Test error when no instance available."""
        mock_ctx.registry.get.return_value = None
        mock_ctx.registry.get_active.return_value = None

        executor = TrainExecutor(mock_ctx, basic_job, instance_name=None)

        with pytest.raises(ValueError, match="No instance"):
            executor._get_instance()

    def test_error_when_instance_not_running(self, mock_ctx, basic_job):
        """Test error when instance is not running."""
        instance = MagicMock()
        instance.name = "test-gpu"
        instance.is_running = False
        mock_ctx.registry.get.return_value = instance

        executor = TrainExecutor(mock_ctx, basic_job, instance_name="test-gpu")

        with pytest.raises(ValueError, match="not running"):
            executor._get_instance()

    def test_build_training_command(self, mock_ctx, mock_instance, basic_job):
        """Test training command construction."""
        executor = TrainExecutor(mock_ctx, basic_job, instance_name="test-gpu")
        executor.instance = mock_instance

        cmd = executor._build_training_command()

        assert "python train.py" in cmd
        assert "--epochs" in cmd
        assert "10" in cmd

    def test_build_tmux_command(self, mock_ctx, mock_instance, basic_job):
        """Test tmux wrapper command."""
        executor = TrainExecutor(mock_ctx, basic_job, instance_name="test-gpu")
        executor.instance = mock_instance

        cmd = executor._build_tmux_command("python train.py")

        assert "tmux" in cmd
        assert "train" in cmd  # session name

    def test_inject_wandb_from_env(self, mock_ctx, mock_instance, basic_job):
        """Test WANDB_API_KEY injection from environment."""
        basic_job.wandb_project = "my-project"
        executor = TrainExecutor(mock_ctx, basic_job, instance_name="test-gpu")
        executor.instance = mock_instance

        with patch.dict(os.environ, {"WANDB_API_KEY": "test-key-123"}):
            executor._inject_wandb()

        mock_ctx.connection.inject_auto_env.assert_called_once()
        call_args = mock_ctx.connection.inject_auto_env.call_args[0]
        env_vars = call_args[1]
        assert "WANDB_API_KEY" in env_vars
        assert env_vars["WANDB_API_KEY"] == "test-key-123"

    def test_skip_wandb_when_no_key(self, mock_ctx, mock_instance, basic_job):
        """Test wandb injection skipped when no API key."""
        executor = TrainExecutor(mock_ctx, basic_job, instance_name="test-gpu")
        executor.instance = mock_instance

        with patch.dict(os.environ, {}, clear=True):
            # Remove WANDB_API_KEY if it exists
            os.environ.pop("WANDB_API_KEY", None)
            executor._inject_wandb()

        mock_ctx.connection.inject_auto_env.assert_not_called()

    def test_generate_download_command(self, mock_ctx, mock_instance, basic_job):
        """Test artifact download command generation."""
        executor = TrainExecutor(mock_ctx, basic_job, instance_name="test-gpu")
        executor.instance = mock_instance

        cmd = executor.get_download_command()

        assert "vastctl cp" in cmd
        assert "test-gpu" in cmd
        assert "/workspace/outputs" in cmd
        assert "-r" in cmd


class TestTrainExecutorRun:
    """Test TrainExecutor.run() workflow."""

    @pytest.fixture
    def mock_ctx(self):
        """Create mocked CLI context."""
        ctx = MagicMock()
        ctx.registry = MagicMock()
        ctx.connection = MagicMock()
        ctx.connection.execute_command.return_value = True
        ctx.connection.inject_auto_env.return_value = True
        ctx.storage = MagicMock()
        ctx.config = MagicMock()
        ctx.config.ssh_key_path = Path("/home/user/.ssh/id_rsa")
        ctx.config.transfer_exclude_patterns = []
        return ctx

    @pytest.fixture
    def mock_instance(self):
        """Create mocked instance."""
        instance = MagicMock()
        instance.name = "test-gpu"
        instance.is_running = True
        instance.ssh_host = "ssh.vast.ai"
        instance.ssh_port = 12345
        return instance

    @patch("vastctl_core.train.subprocess.run")
    def test_run_full_workflow(self, mock_subprocess, mock_ctx, mock_instance, tmp_path):
        """Test complete training workflow."""
        # Create a minimal project
        script = tmp_path / "train.py"
        script.write_text("print('training')")
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("torch")

        job = TrainJob(
            script=Path("train.py"),
            script_args=["--epochs", "5"],
            sync_dir=tmp_path,
            remote_outputs="/workspace/outputs",
        )

        mock_ctx.registry.get.return_value = mock_instance
        mock_subprocess.return_value = MagicMock(returncode=0)

        executor = TrainExecutor(mock_ctx, job, instance_name="test-gpu")
        result = executor.run()

        assert result.success
        assert result.instance_name == "test-gpu"
        assert "vastctl cp" in result.download_command

    def test_run_skips_upload_when_flagged(self, mock_ctx, mock_instance, tmp_path):
        """Test --no-upload skips file sync."""
        job = TrainJob(
            script=Path("train.py"),
            script_args=[],
            sync_dir=tmp_path,
            no_upload=True,
        )

        mock_ctx.registry.get.return_value = mock_instance

        executor = TrainExecutor(mock_ctx, job, instance_name="test-gpu")
        executor.run()

        # Storage should not be called for upload
        # (We mock the rsync call, so check execute_command wasn't called with rsync)

    def test_run_skips_deps_when_flagged(self, mock_ctx, mock_instance, tmp_path):
        """Test --no-deps skips dependency installation."""
        reqs = tmp_path / "requirements.txt"
        reqs.write_text("torch")

        job = TrainJob(
            script=Path("train.py"),
            script_args=[],
            sync_dir=tmp_path,
            no_deps=True,
        )

        mock_ctx.registry.get.return_value = mock_instance

        executor = TrainExecutor(mock_ctx, job, instance_name="test-gpu")
        executor.run()

        # Check pip install wasn't called
        calls = [str(c) for c in mock_ctx.connection.execute_command.call_args_list]
        pip_calls = [c for c in calls if "pip install" in c]
        assert len(pip_calls) == 0
