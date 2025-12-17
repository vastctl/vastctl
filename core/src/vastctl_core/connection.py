"""Connection management for VastLab"""

import base64
import os
import sys
import subprocess
import time
import webbrowser
import random
import string
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import paramiko
import logging

from .config import Config
from .instance import Instance

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manage SSH connections and tunnels"""
    
    def __init__(self, config: Config):
        self.config = config
        self.tunnels: Dict[str, subprocess.Popen] = {}
        self._tunnel_pids: Dict[str, int] = {}
    
    def ssh_connect(
        self,
        instance: Instance,
        command: Optional[str] = None,
        tmux: bool = False,
        tmux_new: bool = False,
    ):
        """Open SSH connection to instance.

        Args:
            instance: Instance to connect to
            command: Optional command to run
            tmux: If True, attach to or create tmux session
            tmux_new: If True, create new tmux window (implies tmux=True)
        """
        if not instance.ssh_host or not instance.ssh_port:
            raise ValueError(f"No SSH connection info for instance '{instance.name}'")

        ssh_key = self.config.ssh_key_path
        if not ssh_key.exists():
            raise FileNotFoundError(f"SSH key not found: {ssh_key}")

        instance.mark_accessed()

        ssh_cmd = [
            "ssh",
            "-t",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-i", str(ssh_key),
            "-p", str(instance.ssh_port),
            f"root@{instance.ssh_host}",
        ]

        if tmux or tmux_new:
            if tmux_new:
                # Create new window if session exists, otherwise create session
                remote_cmd = (
                    "bash -lc '"
                    "tmux has-session -t vastlab 2>/dev/null && "
                    "tmux new-window -t vastlab \\; attach-session -t vastlab || "
                    "tmux new-session -s vastlab'"
                )
            else:
                # Attach to existing or create new session
                remote_cmd = "bash -lc 'tmux attach-session -t vastlab || tmux new-session -s vastlab'"
            ssh_cmd.append(remote_cmd)
        elif command:
            ssh_cmd.append(command)
        else:
            # Plain SSH - disable Vast.ai's auto-tmux, start in workspace
            # Workspace is symlinked at ~/workspace during setup
            ssh_cmd.append("touch ~/.no_auto_tmux; cd ~/workspace 2>/dev/null || cd ~; exec bash")

        os.execvp("ssh", ssh_cmd)
    
    def setup_tunnel(self, instance: Instance, local_port: int = 8888, 
                    remote_port: int = 8888) -> bool:
        """Setup SSH tunnel for port forwarding"""
        if not instance.ssh_host or not instance.ssh_port:
            raise ValueError(f"No SSH connection info for instance '{instance.name}'")
        
        ssh_key = self.config.ssh_key_path
        if not ssh_key.exists():
            raise FileNotFoundError(f"SSH key not found: {ssh_key}")
        
        # Close existing tunnel if any
        self.close_tunnel(instance.name)
        
        # Build tunnel command
        tunnel_cmd = [
            "ssh",
            "-N",  # No command
            "-L", f"{local_port}:localhost:{remote_port}",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ServerAliveInterval=60",
            "-o", "ServerAliveCountMax=3",
            "-i", str(ssh_key),
            "-p", str(instance.ssh_port),
            f"root@{instance.ssh_host}"
        ]
        
        logger.info(f"Setting up SSH tunnel for {instance.name} on port {local_port}")
        
        # Start tunnel
        tunnel = subprocess.Popen(
            tunnel_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        self.tunnels[instance.name] = tunnel
        self._tunnel_pids[instance.name] = tunnel.pid
        
        # Give it time to establish
        time.sleep(5)
        
        # Check if tunnel is running
        if tunnel.poll() is not None:
            stderr = tunnel.stderr.read().decode() if tunnel.stderr else ""
            logger.error(f"SSH tunnel failed: {stderr}")
            return False
        
        logger.info(f"SSH tunnel established (PID: {tunnel.pid})")
        return True
    
    def close_tunnel(self, instance_name: str):
        """Close SSH tunnel"""
        if instance_name in self.tunnels:
            tunnel = self.tunnels[instance_name]
            try:
                tunnel.terminate()
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
            del self.tunnels[instance_name]
            
            if instance_name in self._tunnel_pids:
                del self._tunnel_pids[instance_name]
            
            logger.info(f"Closed SSH tunnel for {instance_name}")
    
    def close_all_tunnels(self):
        """Close all SSH tunnels"""
        for instance_name in list(self.tunnels.keys()):
            self.close_tunnel(instance_name)
    
    def test_connection(self, instance: Instance) -> bool:
        """Test SSH connection to instance"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            ssh.connect(
                hostname=instance.ssh_host,
                port=instance.ssh_port,
                username="root",
                key_filename=str(self.config.ssh_key_path),
                timeout=10
            )
            
            # Test command
            stdin, stdout, stderr = ssh.exec_command("echo 'Connection test'")
            result = stdout.read().decode().strip()
            
            ssh.close()
            return result == "Connection test"
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False
    
    def open_jupyter(self, instance: Instance, port: int = 8888) -> bool:
        """Open Jupyter in browser"""
        # First check if Jupyter is running
        if not self.check_jupyter_running(instance):
            logger.warning("Jupyter not running on instance, waiting 30 seconds...")
            time.sleep(30)
            
            # Check again
            if not self.check_jupyter_running(instance):
                logger.error("Jupyter still not running")
                return False
        
        # Setup tunnel
        if not self.setup_tunnel(instance, local_port=port, remote_port=instance.jupyter_port or 8888):
            return False
        
        # Open browser
        url = f"http://localhost:{port}/lab"
        if instance.jupyter_token:
            url += f"?token={instance.jupyter_token}"
        
        logger.info(f"Opening Jupyter at {url}")
        webbrowser.open(url)
        
        # Also copy URL to clipboard if possible
        try:
            if sys.platform == "darwin":  # macOS
                subprocess.run(["pbcopy"], input=url.encode(), check=True)
                logger.info("URL copied to clipboard")
        except:
            pass
        
        return True
    
    def check_jupyter_running(self, instance: Instance) -> bool:
        """Check if Jupyter is running on the instance"""
        # Try subprocess first to avoid paramiko key issues
        port = instance.jupyter_port or 8888
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-i", str(self.config.ssh_key_path),
            "-p", str(instance.ssh_port),
            f"root@{instance.ssh_host}",
            f"curl -s http://localhost:{port}/api"
        ]
        
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                return "version" in result.stdout
            else:
                # Fallback to checking if process exists
                check_cmd = ssh_cmd[:-1] + ["pgrep -f jupyter-lab"]
                result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
                return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning("SSH command timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to check Jupyter status: {e}")
            return False
    
    def generate_jupyter_token(self) -> str:
        """Generate a random Jupyter token"""
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=32))
    
    def get_storage_workspace_cmd(self) -> str:
        """Get command to find and use largest storage"""
        return """
        # Find the largest storage directory
        STORAGE_DIR=$(df -h | grep -v overlay | grep -v tmpfs | awk 'NR>1 {print $6, $4}' | sort -k2 -hr | head -1 | awk '{print $1}')
        
        # Create workspace in the large storage
        if [ -n "$STORAGE_DIR" ] && [ -d "$STORAGE_DIR" ]; then
            mkdir -p $STORAGE_DIR/workspace
            cd $STORAGE_DIR/workspace
            echo "Using storage at: $STORAGE_DIR/workspace"
        else
            # Fallback to /tmp if no large storage found
            mkdir -p /tmp/workspace
            cd /tmp/workspace
            echo "Using fallback storage at: /tmp/workspace"
        fi
        """
    
    def execute_remote_command(self, instance: Instance, command: str) -> Tuple[str, str]:
        """Execute command on remote instance"""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh.connect(
                hostname=instance.ssh_host,
                port=instance.ssh_port,
                username="root",
                key_filename=str(self.config.ssh_key_path),
                timeout=30
            )
            
            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode()
            error = stderr.read().decode()
            
            ssh.close()
            return output, error
            
        except Exception as e:
            logger.error(f"Remote command failed: {e}")
            return "", str(e)
    
    def execute_command(self, instance: Instance, command: str, description: str = "") -> bool:
        """Execute command on remote instance and return success status"""
        # Try subprocess first to avoid paramiko key issues
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-i", str(self.config.ssh_key_path),
            "-p", str(instance.ssh_port),
            f"root@{instance.ssh_host}",
            command
        ]
        
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                if result.stdout:
                    logger.info(f"Command output: {result.stdout.strip()}")
                return True
            else:
                if result.stderr and "WARNING:" not in result.stderr:
                    logger.error(f"Command error: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            logger.error("Command timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to execute command: {e}")
            # Fallback to paramiko method
            try:
                output, error = self.execute_remote_command(instance, command)
                if error and "WARNING:" not in error:
                    logger.error(f"Command error: {error}")
                    return False
                if output:
                    logger.info(f"Command output: {output.strip()}")
                return True
            except Exception as e2:
                logger.error(f"Both SSH methods failed: {e2}")
                return False

    def restart_jupyter(self, instance: Instance, token: str, port: int = 8888) -> bool:
        """Restart Jupyter Lab on the instance.

        Kills any existing jupyter processes, installs minimal essentials,
        and starts a new Jupyter Lab instance.

        Args:
            instance: Instance to restart Jupyter on
            token: Jupyter authentication token
            port: Port to run Jupyter on (default 8888)

        Returns:
            True if Jupyter was restarted successfully
        """
        script = f'''#!/bin/bash
set -e

# Kill existing jupyter processes
pkill -f jupyter-lab || true
pkill -f jupyter || true
sleep 2

# Minimal essentials (avoid heavy installs here)
if ! command -v unzip >/dev/null 2>&1; then
    apt-get update && apt-get install -y zip unzip
fi

# Use python -m pip for consistency
python -m pip install -q -U jupyterlab notebook ipywidgets

# Find workspace directory
cd /workspace 2>/dev/null || cd /tmp/workspace 2>/dev/null || cd /root

# Start Jupyter in background
nohup jupyter lab \\
    --ip=0.0.0.0 \\
    --port={port} \\
    --no-browser \\
    --allow-root \\
    --NotebookApp.token='{token}' \\
    --NotebookApp.password='' \\
    --ServerApp.disable_check_xsrf=True \\
    --notebook-dir=. \\
    > /tmp/jupyter.log 2>&1 &

echo "Jupyter restarted on port {port}"
'''
        return self.execute_command(instance, script, description="Restarting Jupyter")

    def inject_env_file(self, instance: Instance, env_content: str,
                        env_file: str = "/root/.env") -> bool:
        """Inject environment variables via SSH (never sent to Vast API).

        SECURITY: This method injects secrets directly over SSH after the
        instance is running, keeping secrets out of Vast's API/metadata.
        Uses base64 encoding to safely transmit content with special characters.

        Args:
            instance: Instance to inject secrets into
            env_content: Raw content for the env file (key=value lines)
            env_file: Path to write the env file (default: /root/.env)

        Returns:
            True if injection succeeded
        """
        if not env_content.strip():
            return True

        # Base64 encode the content to safely pass any special characters
        b64_content = base64.b64encode(env_content.encode()).decode()

        # Remote script decodes base64 and writes to file
        script = f"""umask 077 && echo '{b64_content}' | base64 -d > {env_file} && chmod 600 {env_file} && if ! grep -q 'source {env_file}' /root/.bashrc 2>/dev/null; then echo '' >> /root/.bashrc && echo '# Load injected environment variables' >> /root/.bashrc && echo 'set -a; source {env_file}; set +a' >> /root/.bashrc; fi && echo 'Environment injected to {env_file}'"""
        return self.execute_command(instance, script, description="Injecting environment")

    def inject_auto_env(self, instance: Instance, env_vars: dict) -> bool:
        """Inject auto-detected credentials via SSH.

        SECURITY: Credentials are injected over SSH, never sent to Vast API.
        Uses base64 encoding to safely transmit content with special characters.

        Args:
            instance: Instance to inject secrets into
            env_vars: Dict of environment variable names to values

        Returns:
            True if injection succeeded
        """
        if not env_vars:
            return True

        # Build export statements
        lines = []
        for key, value in sorted(env_vars.items()):
            # Escape single quotes in value for shell
            escaped_value = value.replace("'", "'\\''")
            lines.append(f"export {key}='{escaped_value}'")

        env_content = "\n".join(lines)

        # Base64 encode the content to safely pass any special characters
        b64_content = base64.b64encode(env_content.encode()).decode()
        num_vars = len(env_vars)

        # Remote script decodes base64 and writes to file
        script = f"""umask 077 && echo '{b64_content}' | base64 -d > /root/.auto_env && chmod 600 /root/.auto_env && if ! grep -q 'source /root/.auto_env' /root/.bashrc 2>/dev/null; then echo '' >> /root/.bashrc && echo '# Auto-injected credentials from vastlab' >> /root/.bashrc && echo 'source /root/.auto_env' >> /root/.bashrc; fi && echo 'Auto-env injected ({num_vars} variables)'"""
        return self.execute_command(instance, script, description="Injecting auto-env")