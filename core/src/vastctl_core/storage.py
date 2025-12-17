"""Storage management for VastLab"""

import os
import subprocess
import tarfile
import zipfile
import tempfile
import shutil
import shlex
import json
import asyncio
import concurrent.futures
import fnmatch
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
import paramiko
import logging

from .config import Config
from .instance import Instance

logger = logging.getLogger(__name__)


class StorageManager:
    """Manage instance storage and workspaces"""
    
    def __init__(self, config: Config):
        self.config = config
    
    def get_storage_info(self, instance: Instance) -> Dict[str, Any]:
        """Get storage information from instance"""
        if not instance.ssh_host or not instance.ssh_port:
            raise ValueError(f"No SSH connection info for instance '{instance.name}'")
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh.connect(
                hostname=instance.ssh_host,
                port=instance.ssh_port,
                username="root",
                key_filename=str(self.config.ssh_key_path),
                timeout=10
            )
            
            # Get disk usage
            stdin, stdout, stderr = ssh.exec_command("df -h")
            df_output = stdout.read().decode()
            
            # Find largest storage
            stdin, stdout, stderr = ssh.exec_command(
                'df -h | grep -v overlay | grep -v tmpfs | awk "NR>1 {print $6, $4}" | sort -k2 -hr | head -1'
            )
            largest = stdout.read().decode().strip()
            
            # Get workspace info
            workspace_cmd = (
                'STORAGE_DIR=$(df -h | grep -v overlay | grep -v tmpfs | '
                'awk "NR>1 {print $6, $4}" | sort -k2 -hr | head -1 | awk "{print $1}"); '
                'if [ -d "$STORAGE_DIR/workspace" ]; then '
                'echo "$STORAGE_DIR/workspace"; '
                'du -sh "$STORAGE_DIR/workspace" 2>/dev/null | awk "{print $1}"; '
                'else echo "No workspace"; fi'
            )
            stdin, stdout, stderr = ssh.exec_command(workspace_cmd)
            workspace_info = stdout.read().decode().strip().split('\n')
            
            ssh.close()
            
            result = {
                'df_output': df_output,
                'largest_mount': largest.split()[0] if largest else None,
                'largest_available': largest.split()[1] if largest else None,
                'workspace_path': workspace_info[0] if workspace_info else None,
                'workspace_size': workspace_info[1] if len(workspace_info) > 1 else None,
            }
            
            # Parse df output for structured data
            lines = df_output.strip().split('\n')[1:]  # Skip header
            mounts = []
            for line in lines:
                parts = line.split()
                if len(parts) >= 6:
                    mounts.append({
                        'filesystem': parts[0],
                        'size': parts[1],
                        'used': parts[2],
                        'available': parts[3],
                        'use_percent': parts[4],
                        'mount': parts[5]
                    })
            result['mounts'] = mounts
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get storage info: {e}")
            return {'error': str(e)}
    
    def setup_workspace(self, instance: Instance) -> bool:
        """Setup workspace on instance with largest storage"""
        if not instance.ssh_host or not instance.ssh_port:
            raise ValueError(f"No SSH connection info for instance '{instance.name}'")
        
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
            
            # Setup workspace script
            setup_script = """
            # Find the largest writable storage directory
            # Use df with 1K blocks for numeric sorting, filter for real data mounts
            STORAGE_DIR=""

            # Try common Vast.ai data mount locations first
            for dir in /var/lib/docker /home /data /mnt/data /tmp; do
                if [ -d "$dir" ] && [ -w "$dir" ]; then
                    # Check if it has significant space (>10GB)
                    avail=$(df -k "$dir" 2>/dev/null | awk 'NR==2 {print $4}')
                    if [ -n "$avail" ] && [ "$avail" -gt 10000000 ]; then
                        STORAGE_DIR="$dir"
                        break
                    fi
                fi
            done

            # Fallback: find largest mount with >10GB
            if [ -z "$STORAGE_DIR" ]; then
                STORAGE_DIR=$(df -k | awk 'NR>1 && $4>10000000 {print $4, $6}' | grep -v -E '(overlay|tmpfs|/dev|/proc|/sys|/etc)' | sort -rn | head -1 | awk '{print $2}')
            fi

            # Final fallback to /tmp
            if [ -z "$STORAGE_DIR" ] || [ ! -w "$STORAGE_DIR" ]; then
                STORAGE_DIR="/tmp"
            fi

            # Create workspace directory
            WORKSPACE_DIR="$STORAGE_DIR/workspace"
            mkdir -p "$WORKSPACE_DIR"
            
            # Create subdirectories
            mkdir -p "$WORKSPACE_DIR/models"
            mkdir -p "$WORKSPACE_DIR/datasets"
            mkdir -p "$WORKSPACE_DIR/outputs"
            mkdir -p "$WORKSPACE_DIR/checkpoints"
            mkdir -p "$WORKSPACE_DIR/logs"
            mkdir -p "$WORKSPACE_DIR/notebooks"
            
            # Create symlinks
            rm -f ~/workspace
            ln -s "$WORKSPACE_DIR" ~/workspace
            
            if [ -d "/workspace" ] && [ ! -L "/workspace" ]; then
                if [ "$(ls -A /workspace 2>/dev/null)" ]; then
                    mv /workspace/* "$WORKSPACE_DIR/" 2>/dev/null || true
                fi
                rm -rf /workspace
            fi
            ln -sfn "$WORKSPACE_DIR" /workspace
            
            # Set environment
            echo "export WORKSPACE=$WORKSPACE_DIR" >> ~/.bashrc
            echo "export HF_HOME=$WORKSPACE_DIR/models" >> ~/.bashrc
            echo "export TRANSFORMERS_CACHE=$WORKSPACE_DIR/models" >> ~/.bashrc
            echo "export HF_DATASETS_CACHE=$WORKSPACE_DIR/datasets" >> ~/.bashrc
            
            echo "SUCCESS: Workspace setup at $WORKSPACE_DIR"
            """
            
            stdin, stdout, stderr = ssh.exec_command(setup_script)
            output = stdout.read().decode()
            error = stderr.read().decode()
            
            ssh.close()
            
            if "SUCCESS" in output:
                # Extract workspace path
                for line in output.split('\n'):
                    if "SUCCESS: Workspace setup at" in line:
                        workspace_path = line.split("at ")[-1].strip()
                        instance.storage_path = workspace_path
                        logger.info(f"Workspace setup completed at {workspace_path}")
                        return True
            
            logger.error(f"Workspace setup failed: {error}")
            return False
            
        except Exception as e:
            logger.error(f"Failed to setup workspace: {e}")
            return False
    
    def copy_to_instance(self, instance: Instance, local_path: str, remote_path: str, bandwidth_limit: int = None) -> bool:
        """Copy a file from local to instance using SCP

        Args:
            bandwidth_limit: Optional bandwidth limit in KB/s
        """
        if not instance.ssh_host or not instance.ssh_port:
            logger.error(f"No SSH connection info for instance '{instance.name}'")
            return False

        try:
            # Ensure remote directory exists
            remote_dir = os.path.dirname(remote_path)
            if remote_dir and remote_dir != '/':
                mkdir_cmd = [
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "LogLevel=ERROR",
                    "-i", str(self.config.ssh_key_path),
                    "-p", str(instance.ssh_port),
                    f"root@{instance.ssh_host}",
                    f"mkdir -p '{remote_dir}'"
                ]
                subprocess.run(mkdir_cmd, capture_output=True)

            # Use SCP to copy file
            scp_cmd = [
                "scp",
                "-o", "StrictHostKeyChecking=no",
                "-o", "LogLevel=ERROR",
                "-i", str(self.config.ssh_key_path),
                "-P", str(instance.ssh_port),
            ]

            # Add bandwidth limit if specified (scp -l takes Kbit/s, so KB/s * 8)
            if bandwidth_limit:
                scp_cmd.extend(["-l", str(bandwidth_limit * 8)])

            scp_cmd.extend([
                local_path,
                f"root@{instance.ssh_host}:{remote_path}"
            ])

            timeout_sec = int(self.config.get('transfer.timeout_seconds', 300) or 300)
            # Disable timeout when bandwidth limited (transfer will take longer)
            if bandwidth_limit:
                timeout_sec = None
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=timeout_sec)

            if result.returncode == 0:
                logger.info(f"Successfully copied {local_path} to {instance.name}:{remote_path}")
                return True
            else:
                logger.error(f"SCP failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"File copy timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to copy file: {e}")
            return False
    
    def get_file_size(self, path: str) -> int:
        """Get file size in bytes"""
        try:
            return os.path.getsize(path)
        except (OSError, FileNotFoundError):
            return 0
    
    def should_skip_file(self, file_path: str, force_include: bool = False, max_size_mb: int = None) -> Tuple[bool, str]:
        """Check if a file should be skipped based on size and patterns"""
        # If force_include is set, bypass all checks
        if force_include:
            return False, "Force include enabled"

        # Quick check exclude patterns first (faster than file size)
        file_name = os.path.basename(file_path)
        for pattern in self.config.transfer_exclude_patterns:
            # Use proper glob matching with fnmatch
            if fnmatch.fnmatch(file_name, pattern) or fnmatch.fnmatch(file_path, pattern):
                return True, f"Matches exclude pattern: {pattern}"

        # Check size limit only if needed
        if self.config.ignore_large_files:
            try:
                # Use os.stat which is faster than os.path.getsize
                stat_result = os.stat(file_path)
                size_bytes = stat_result.st_size
                size_mb = size_bytes / (1024 * 1024)

                effective_max_size = max_size_mb if max_size_mb is not None else self.config.max_file_size_mb
                if size_mb > effective_max_size:
                    return True, f"File too large ({size_mb:.1f}MB > {effective_max_size}MB)"
            except (OSError, FileNotFoundError):
                return True, "File not accessible"

        return False, "OK"
    
    def copy_file_worker(self, args: Tuple) -> Dict[str, Any]:
        """Worker function for parallel file copying"""
        instance, local_file, remote_file, force_include = args
        
        result = {
            "local_file": str(local_file),
            "success": False,
            "size_mb": 0,
            "error": None,
            "skipped": False,
            "skip_reason": None
        }
        
        try:
            # Check if file should be skipped
            should_skip, reason = self.should_skip_file(str(local_file), force_include)
            if should_skip:
                result["skipped"] = True
                result["skip_reason"] = reason
                result["size_mb"] = self.get_file_size(str(local_file)) / (1024 * 1024)
                return result
            
            # Copy the file (copy_to_instance already handles directory creation)
            success = self.copy_to_instance(instance, str(local_file), remote_file)
            if success:
                result["success"] = True
                result["size_mb"] = self.get_file_size(str(local_file)) / (1024 * 1024)
            else:
                result["error"] = "SCP transfer failed"
                
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    def copy_recursive_to_instance_parallel(self, instance: Instance, local_path: str, remote_path: str, 
                                          force_include: bool = False, max_workers: int = 4) -> Dict[str, Any]:
        """Copy directory recursively to instance using zip compression (parallel not needed for zip)"""
        # For zip-based transfers, parallel processing doesn't provide benefits since we're
        # creating a single compressed file. Use the zip method directly.
        return self.copy_recursive_to_instance_zip(instance, local_path, remote_path, force_include)
    
    def copy_recursive_to_instance(self, instance: Instance, local_path: str, remote_path: str,
                                  force_include: bool = False, max_size_mb: int = None) -> Dict[str, Any]:
        """Copy directory recursively to instance using zip compression"""
        return self.copy_recursive_to_instance_zip(instance, local_path, remote_path, force_include, max_size_mb)
    
    def copy_recursive_to_instance_zip(self, instance: Instance, local_path: str, remote_path: str,
                                      force_include: bool = False, max_size_mb: int = None) -> Dict[str, Any]:
        """Copy directory recursively to instance using zip compression"""
        if not instance.ssh_host or not instance.ssh_port:
            logger.error(f"No SSH connection info for instance '{instance.name}'")
            return {"success": False, "error": "No SSH connection info"}
        
        local_path = Path(local_path)
        if not local_path.exists():
            return {"success": False, "error": f"Local path does not exist: {local_path}"}
        
        results = {
            "success": True,
            "files_copied": [],
            "files_skipped": [],
            "total_size_mb": 0,
            "errors": []
        }
        
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                zip_path = Path(tmpdir) / "transfer.zip"
                
                # Create zip file
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    if local_path.is_file():
                        # Single file
                        should_skip, reason = self.should_skip_file(str(local_path), force_include, max_size_mb)
                        if should_skip:
                            results["files_skipped"].append({
                                "path": str(local_path),
                                "reason": reason,
                                "size_mb": self.get_file_size(str(local_path)) / (1024 * 1024)
                            })
                        else:
                            zipf.write(local_path, local_path.name)
                            size_mb = self.get_file_size(str(local_path)) / (1024 * 1024)
                            results["files_copied"].append({
                                "path": str(local_path),
                                "size_mb": size_mb
                            })
                            results["total_size_mb"] += size_mb
                    else:
                        # Directory - preserve folder structure
                        folder_name = local_path.name

                        for file_path in local_path.rglob('*'):
                            if file_path.is_file():
                                should_skip, reason = self.should_skip_file(str(file_path), force_include, max_size_mb)
                                if should_skip:
                                    relative_path = file_path.relative_to(local_path.parent)
                                    results["files_skipped"].append({
                                        "path": str(relative_path),
                                        "reason": reason,
                                        "size_mb": self.get_file_size(str(file_path)) / (1024 * 1024)
                                    })
                                else:
                                    # Store files with folder structure: folder_name/relative_path
                                    relative_path = file_path.relative_to(local_path.parent)
                                    zipf.write(file_path, str(relative_path))
                                    size_mb = self.get_file_size(str(file_path)) / (1024 * 1024)
                                    results["files_copied"].append({
                                        "path": str(relative_path),
                                        "size_mb": size_mb
                                    })
                                    results["total_size_mb"] += size_mb
                
                # If no files were added to zip, return early
                if not results["files_copied"]:
                    if results["files_skipped"]:
                        return results  # All files were skipped
                    else:
                        return {"success": False, "error": "No files to copy"}
                
                # Compute archive size for logging
                try:
                    results["zip_size_mb"] = Path(zip_path).stat().st_size / (1024 * 1024)
                except Exception:
                    results["zip_size_mb"] = 0

                # Upload zip file to remote
                remote_zip_path = f"/tmp/vastlab_transfer_{os.getpid()}.zip"
                if not self.copy_to_instance(instance, str(zip_path), remote_zip_path):
                    return {"success": False, "error": "Failed to upload zip file"}
                
                # Create target directory on remote
                mkdir_cmd = [
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "LogLevel=ERROR",
                    "-i", str(self.config.ssh_key_path),
                    "-p", str(instance.ssh_port),
                    f"root@{instance.ssh_host}",
                    f"mkdir -p '{remote_path}'"
                ]
                mkdir_result = subprocess.run(mkdir_cmd, capture_output=True)
                if mkdir_result.returncode != 0:
                    return {"success": False, "error": f"Failed to create remote directory: {mkdir_result.returncode}"}
                
                # Extract zip on remote (try unzip, fall back to Python if not available)
                extract_script = f"""
if command -v unzip >/dev/null 2>&1; then
    cd '{remote_path}' && unzip -o '{remote_zip_path}' && rm '{remote_zip_path}' && echo 'UNZIP_COMPLETE'
else
    python3 -c "import zipfile; z=zipfile.ZipFile('{remote_zip_path}'); z.extractall('{remote_path}'); z.close(); print('UNZIP_COMPLETE')" && rm '{remote_zip_path}'
fi
"""
                unzip_cmd = [
                    "ssh",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "LogLevel=ERROR",  # Suppress connection messages
                    "-i", str(self.config.ssh_key_path),
                    "-p", str(instance.ssh_port),
                    f"root@{instance.ssh_host}",
                    f"bash -c {shlex.quote(extract_script)}"
                ]

                result = subprocess.run(unzip_cmd, capture_output=True, text=True)
                if result.returncode != 0 or "UNZIP_COMPLETE" not in result.stdout:
                    # Clean up remote zip file on error
                    cleanup_cmd = [
                        "ssh",
                        "-o", "StrictHostKeyChecking=no",
                        "-o", "LogLevel=ERROR",
                        "-i", str(self.config.ssh_key_path),
                        "-p", str(instance.ssh_port),
                        f"root@{instance.ssh_host}",
                        f"rm -f '{remote_zip_path}'"
                    ]
                    subprocess.run(cleanup_cmd, capture_output=True)
                    return {"success": False, "error": f"Failed to extract zip (return code: {result.returncode})"}
                
        except Exception as e:
            results["success"] = False
            results["error"] = str(e)
            logger.error(f"Error in zip-based recursive copy: {e}")
        
        return results
    
    def copy_from_instance(self, instance: Instance, remote_path: str, local_path: str, bandwidth_limit: int = None) -> bool:
        """Copy a file from instance to local using SCP

        Args:
            bandwidth_limit: Optional bandwidth limit in KB/s
        """
        if not instance.ssh_host or not instance.ssh_port:
            logger.error(f"No SSH connection info for instance '{instance.name}'")
            return False

        try:
            # Ensure local directory exists
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)

            # Use SCP to copy file
            scp_cmd = [
                "scp",
                "-o", "StrictHostKeyChecking=no",
                "-o", "LogLevel=ERROR",
                "-i", str(self.config.ssh_key_path),
                "-P", str(instance.ssh_port),
            ]

            # Add bandwidth limit if specified (scp -l takes Kbit/s, so KB/s * 8)
            if bandwidth_limit:
                scp_cmd.extend(["-l", str(bandwidth_limit * 8)])

            scp_cmd.extend([
                f"root@{instance.ssh_host}:{remote_path}",
                local_path
            ])

            timeout_sec = int(self.config.get('transfer.timeout_seconds', 300) or 300)
            # Disable timeout when bandwidth limited (transfer will take longer)
            if bandwidth_limit:
                timeout_sec = None
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=timeout_sec)

            if result.returncode == 0:
                logger.info(f"Successfully copied {instance.name}:{remote_path} to {local_path}")
                return True
            else:
                logger.error(f"SCP failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"File copy timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to copy file: {e}")
            return False
    
    def backup_instance(self, instance: Instance, patterns: List[str] = None,
                       exclude_patterns: List[str] = None) -> Optional[Path]:
        """Backup instance data to local storage"""
        if not instance.ssh_host or not instance.ssh_port:
            raise ValueError(f"No SSH connection info for instance '{instance.name}'")
        
        # Default patterns
        if patterns is None:
            patterns = ["*.pt", "*.pth", "*.safetensors", "*.ckpt", "*.h5", 
                       "notebooks/*.ipynb", "*.json", "*.yaml", "*.txt"]
        
        if exclude_patterns is None:
            exclude_patterns = ["*__pycache__*", "*.pyc", ".git/*"]
        
        # Create backup directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.config.backup_path / instance.name / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Create metadata
        metadata = {
            'instance': instance.to_dict(),
            'timestamp': timestamp,
            'patterns': patterns,
            'exclude_patterns': exclude_patterns,
        }
        
        with open(backup_dir / 'metadata.json', 'w') as f:
            json.dump(metadata, f, indent=2)
        
        logger.info(f"Starting backup of {instance.name} to {backup_dir}")
        
        # Use rsync for efficient backup
        workspace = instance.storage_path or "/workspace"
        
        for pattern in patterns:
            rsync_cmd = [
                "rsync",
                "-avz",
                "--progress",
                "-e", f"ssh -o StrictHostKeyChecking=no -i {self.config.ssh_key_path} -p {instance.ssh_port}",
                f"root@{instance.ssh_host}:{workspace}/{pattern}",
                str(backup_dir)
            ]
            
            # Add exclusions
            for exclude in exclude_patterns:
                rsync_cmd.extend(["--exclude", exclude])
            
            logger.info(f"Backing up {pattern}...")
            result = subprocess.run(rsync_cmd, capture_output=True, text=True)
            
            if result.returncode != 0 and "No such file" not in result.stderr:
                logger.warning(f"Backup of {pattern} failed: {result.stderr}")
        
        # Create tar archive
        archive_path = backup_dir.parent / f"{instance.name}_{timestamp}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(backup_dir, arcname=os.path.basename(backup_dir))
        
        # Clean up directory
        import shutil
        shutil.rmtree(backup_dir)
        
        logger.info(f"Backup completed: {archive_path}")
        return archive_path
    
    def restore_instance(self, instance: Instance, backup_path: Path) -> bool:
        """Restore instance data from backup"""
        if not instance.ssh_host or not instance.ssh_port:
            raise ValueError(f"No SSH connection info for instance '{instance.name}'")
        
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup not found: {backup_path}")
        
        logger.info(f"Restoring {instance.name} from {backup_path}")
        
        # Extract backup
        temp_dir = self.config.backup_path / "temp_restore"
        temp_dir.mkdir(exist_ok=True)
        
        with tarfile.open(backup_path, "r:gz") as tar:
            tar.extractall(temp_dir)
        
        # Find extracted directory
        backup_dir = next(temp_dir.iterdir())
        
        # Load metadata
        with open(backup_dir / 'metadata.json', 'r') as f:
            metadata = json.load(f)
        
        # Restore files
        workspace = instance.storage_path or "/workspace"
        
        for item in backup_dir.iterdir():
            if item.name == 'metadata.json':
                continue
            
            rsync_cmd = [
                "rsync",
                "-avz",
                "--progress",
                "-e", f"ssh -o StrictHostKeyChecking=no -i {self.config.ssh_key_path} -p {instance.ssh_port}",
                str(item),
                f"root@{instance.ssh_host}:{workspace}/"
            ]
            
            logger.info(f"Restoring {item.name}...")
            result = subprocess.run(rsync_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Restore of {item.name} failed: {result.stderr}")
                return False
        
        # Clean up
        import shutil
        shutil.rmtree(temp_dir)
        
        logger.info("Restore completed successfully")
        return True
    
    def sync_instances(self, source: Instance, target: Instance, 
                      patterns: List[str] = None) -> bool:
        """Sync data between instances"""
        if not all([source.ssh_host, source.ssh_port, target.ssh_host, target.ssh_port]):
            raise ValueError("Both instances must have SSH connection info")
        
        if patterns is None:
            patterns = ["datasets/", "models/", "*.json", "*.yaml"]
        
        source_workspace = source.storage_path or "/workspace"
        target_workspace = target.storage_path or "/workspace"
        
        logger.info(f"Syncing from {source.name} to {target.name}")
        
        for pattern in patterns:
            # Use rsync with SSH tunneling
            rsync_cmd = [
                "rsync",
                "-avz",
                "--progress",
                "-e", f"ssh -o StrictHostKeyChecking=no -i {self.config.ssh_key_path} -p {source.ssh_port}",
                f"root@{source.ssh_host}:{source_workspace}/{pattern}",
                "-e", f"ssh -o StrictHostKeyChecking=no -i {self.config.ssh_key_path} -p {target.ssh_port}",
                f"root@{target.ssh_host}:{target_workspace}/"
            ]
            
            logger.info(f"Syncing {pattern}...")
            result = subprocess.run(rsync_cmd, capture_output=True, text=True)
            
            if result.returncode != 0 and "No such file" not in result.stderr:
                logger.error(f"Sync of {pattern} failed: {result.stderr}")
                return False
        
        logger.info("Sync completed successfully")
        return True
    
    def list_backups(self, instance_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """List available backups"""
        backups = []
        
        backup_pattern = "*.tar.gz"
        if instance_name:
            backup_pattern = f"{instance_name}_*.tar.gz"
        
        for backup_file in self.config.backup_path.glob(backup_pattern):
            # Extract info from filename
            parts = backup_file.stem.split('_')
            if len(parts) >= 2:
                name = '_'.join(parts[:-2])
                timestamp = f"{parts[-2]}_{parts[-1]}"
                
                backups.append({
                    'instance': name,
                    'timestamp': timestamp,
                    'path': backup_file,
                    'size': backup_file.stat().st_size,
                    'created': datetime.fromtimestamp(backup_file.stat().st_mtime)
                })
        
        return sorted(backups, key=lambda x: x['created'], reverse=True)
