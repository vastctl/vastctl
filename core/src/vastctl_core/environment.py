"""Environment detection and automated setup for VastLab"""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

from .config import Config
from .instance import Instance
from .connection import ConnectionManager


@dataclass
class SetupCommand:
    """Represents a setup command with metadata"""
    command: str
    description: str
    type: str = "bash"  # bash, pip, apt, conda, etc.
    required: bool = True
    timeout: int = 300  # seconds
    retry_count: int = 1
    conditions: List[str] = field(default_factory=list)  # Conditions to check before running
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'command': self.command,
            'description': self.description,
            'type': self.type,
            'required': self.required,
            'timeout': self.timeout,
            'retry_count': self.retry_count,
            'conditions': self.conditions
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SetupCommand':
        return cls(**data)


@dataclass
class EnvironmentProfile:
    """Environment setup profile for specific hardware/software configurations"""
    name: str
    description: str
    gpu_patterns: List[str] = field(default_factory=list)  # GPU name patterns to match
    cuda_versions: List[str] = field(default_factory=list)  # Supported CUDA versions
    python_versions: List[str] = field(default_factory=list)  # Supported Python versions
    os_patterns: List[str] = field(default_factory=list)  # OS patterns to match
    setup_commands: List[SetupCommand] = field(default_factory=list)
    priority: int = 50  # Higher priority profiles are checked first
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    
    def matches_environment(self, env_info: Dict[str, Any]) -> bool:
        """Check if this profile matches the given environment"""
        # Check GPU patterns
        if self.gpu_patterns:
            gpu_name = env_info.get('gpu_name', '').lower()
            if not any(re.search(pattern.lower(), gpu_name) for pattern in self.gpu_patterns):
                return False
        
        # Check CUDA versions
        if self.cuda_versions:
            cuda_version = env_info.get('cuda_version', '')
            if cuda_version and not any(cuda_version.startswith(v) for v in self.cuda_versions):
                return False
        
        # Check Python versions
        if self.python_versions:
            python_version = env_info.get('python_version', '')
            if python_version and not any(python_version.startswith(v) for v in self.python_versions):
                return False
        
        # Check OS patterns
        if self.os_patterns:
            os_name = env_info.get('os_name', '').lower()
            if not any(re.search(pattern.lower(), os_name) for pattern in self.os_patterns):
                return False
        
        return True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'description': self.description,
            'gpu_patterns': self.gpu_patterns,
            'cuda_versions': self.cuda_versions,
            'python_versions': self.python_versions,
            'os_patterns': self.os_patterns,
            'setup_commands': [cmd.to_dict() for cmd in self.setup_commands],
            'priority': self.priority,
            'tags': self.tags,
            'created_at': self.created_at.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EnvironmentProfile':
        # Parse datetime
        if data.get('created_at'):
            data['created_at'] = datetime.fromisoformat(data['created_at'])
        
        # Parse setup commands
        if 'setup_commands' in data:
            data['setup_commands'] = [SetupCommand.from_dict(cmd) for cmd in data['setup_commands']]
        
        return cls(**data)


class EnvironmentDetector:
    """Detect environment characteristics on remote instances"""
    
    def __init__(self, connection_manager: ConnectionManager):
        self.connection = connection_manager
    
    def detect_environment(self, instance: Instance) -> Dict[str, Any]:
        """Detect environment characteristics on the given instance"""
        env_info = {}
        
        try:
            # Detect GPU information
            gpu_info = self._detect_gpu(instance)
            env_info.update(gpu_info)
            
            # Detect CUDA version
            cuda_info = self._detect_cuda(instance)
            env_info.update(cuda_info)
            
            # Detect Python version
            python_info = self._detect_python(instance)
            env_info.update(python_info)
            
            # Detect OS information
            os_info = self._detect_os(instance)
            env_info.update(os_info)
            
            # Detect installed packages
            package_info = self._detect_packages(instance)
            env_info.update(package_info)
            
        except Exception as e:
            env_info['detection_error'] = str(e)
        
        return env_info
    
    def _detect_gpu(self, instance: Instance) -> Dict[str, Any]:
        """Detect GPU information"""
        try:
            # Try nvidia-smi first
            cmd = "nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits"
            result = self.connection.execute_remote_command(instance, cmd)
            
            if result[0]:  # stdout
                lines = result[0].strip().split('\n')
                if lines:
                    parts = lines[0].split(', ')
                    return {
                        'gpu_name': parts[0] if len(parts) > 0 else 'Unknown',
                        'gpu_memory_mb': int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
                        'nvidia_driver_version': parts[2] if len(parts) > 2 else 'Unknown',
                        'gpu_count': len(lines)
                    }
            
            # Fallback to lspci
            cmd = "lspci | grep -i nvidia"
            result = self.connection.execute_remote_command(instance, cmd)
            if result[0]:
                return {'gpu_name': result[0].strip().split('\n')[0], 'gpu_count': 1}
            
        except Exception:
            pass
        
        return {'gpu_name': 'Unknown', 'gpu_count': 0}
    
    def _detect_cuda(self, instance: Instance) -> Dict[str, Any]:
        """Detect CUDA version"""
        try:
            # Try nvcc first
            cmd = "nvcc --version"
            result = self.connection.execute_remote_command(instance, cmd)
            
            if result[0]:
                for line in result[0].split('\n'):
                    if 'release' in line.lower():
                        # Extract version number
                        match = re.search(r'release (\d+\.\d+)', line)
                        if match:
                            return {'cuda_version': match.group(1)}
            
            # Try nvidia-smi
            cmd = "nvidia-smi | grep -i cuda"
            result = self.connection.execute_remote_command(instance, cmd)
            if result[0]:
                match = re.search(r'CUDA Version: (\d+\.\d+)', result[0])
                if match:
                    return {'cuda_version': match.group(1)}
            
        except Exception:
            pass
        
        return {'cuda_version': 'Unknown'}
    
    def _detect_python(self, instance: Instance) -> Dict[str, Any]:
        """Detect Python version and environment"""
        try:
            cmd = "python --version 2>&1"
            result = self.connection.execute_remote_command(instance, cmd)
            
            python_info = {}
            if result[0]:
                match = re.search(r'Python (\d+\.\d+\.\d+)', result[0])
                if match:
                    python_info['python_version'] = match.group(1)
            
            # Check if we're in a virtual environment
            cmd = "echo $VIRTUAL_ENV"
            result = self.connection.execute_remote_command(instance, cmd)
            if result[0].strip():
                python_info['virtual_env'] = result[0].strip()
            
            # Check if conda is available
            cmd = "conda --version 2>/dev/null"
            result = self.connection.execute_remote_command(instance, cmd)
            if result[0]:
                python_info['conda_available'] = True
                match = re.search(r'conda (\d+\.\d+\.\d+)', result[0])
                if match:
                    python_info['conda_version'] = match.group(1)
            
            return python_info
            
        except Exception:
            pass
        
        return {'python_version': 'Unknown'}
    
    def _detect_os(self, instance: Instance) -> Dict[str, Any]:
        """Detect OS information"""
        try:
            # Get OS release info
            cmd = "cat /etc/os-release"
            result = self.connection.execute_remote_command(instance, cmd)
            
            os_info = {}
            if result[0]:
                for line in result[0].split('\n'):
                    if line.startswith('NAME='):
                        os_info['os_name'] = line.split('=', 1)[1].strip('"')
                    elif line.startswith('VERSION='):
                        os_info['os_version'] = line.split('=', 1)[1].strip('"')
                    elif line.startswith('ID='):
                        os_info['os_id'] = line.split('=', 1)[1].strip('"')
            
            # Get kernel version
            cmd = "uname -r"
            result = self.connection.execute_remote_command(instance, cmd)
            if result[0]:
                os_info['kernel_version'] = result[0].strip()
            
            # Check available package managers
            for pm in ['apt', 'yum', 'dnf', 'pacman']:
                cmd = f"which {pm} 2>/dev/null"
                result = self.connection.execute_remote_command(instance, cmd)
                if result[0].strip():
                    os_info['package_manager'] = pm
                    break
            
            return os_info
            
        except Exception:
            pass
        
        return {'os_name': 'Unknown'}
    
    def _detect_packages(self, instance: Instance) -> Dict[str, Any]:
        """Detect installed packages"""
        try:
            package_info = {}
            
            # Check for PyTorch
            cmd = "python -c 'import torch; print(torch.__version__)' 2>/dev/null"
            result = self.connection.execute_remote_command(instance, cmd)
            if result[0].strip():
                package_info['torch_version'] = result[0].strip()
            
            # Check for TensorFlow
            cmd = "python -c 'import tensorflow as tf; print(tf.__version__)' 2>/dev/null"
            result = self.connection.execute_remote_command(instance, cmd)
            if result[0].strip():
                package_info['tensorflow_version'] = result[0].strip()
            
            # Check for CUDA availability in PyTorch
            cmd = "python -c 'import torch; print(torch.cuda.is_available())' 2>/dev/null"
            result = self.connection.execute_remote_command(instance, cmd)
            if result[0].strip() == 'True':
                package_info['torch_cuda_available'] = True
            
            return package_info
            
        except Exception:
            pass
        
        return {}


class EnvironmentManager:
    """Manage environment profiles and automated setup"""
    
    def __init__(self, config: Config, connection_manager: ConnectionManager):
        self.config = config
        self.connection = connection_manager
        self.detector = EnvironmentDetector(connection_manager)
        self.profiles_file = config.data_dir / "environment_profiles.json"
        self.profiles: Dict[str, EnvironmentProfile] = {}
        self._load_profiles()
        self._create_default_profiles()
    
    def _load_profiles(self):
        """Load environment profiles from disk"""
        if self.profiles_file.exists():
            try:
                with open(self.profiles_file, 'r') as f:
                    data = json.load(f)
                    for profile_name, profile_data in data.items():
                        self.profiles[profile_name] = EnvironmentProfile.from_dict(profile_data)
            except Exception as e:
                logger.warning(f"Failed to load environment profiles: {e}")
    
    def _save_profiles(self):
        """Save environment profiles to disk"""
        self.profiles_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = {name: profile.to_dict() for name, profile in self.profiles.items()}
            with open(self.profiles_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save environment profiles: {e}")
    
    def _create_default_profiles(self):
        """Create default environment profiles"""
        # RTX 5090 Profile
        if 'rtx5090' not in self.profiles:
            rtx5090_profile = EnvironmentProfile(
                name='rtx5090',
                description='RTX 5090 optimized PyTorch setup',
                gpu_patterns=['rtx 5090', 'rtx5090', 'geforce rtx 5090'],
                cuda_versions=['12.8', '12.7'],
                priority=90,
                tags=['pytorch', 'cuda', 'rtx5090'],
                setup_commands=[
                    SetupCommand(
                        command='pip uninstall torch torchvision torchaudio -y',
                        description='Clean existing PyTorch installation',
                        type='pip',
                        required=False
                    ),
                    SetupCommand(
                        command='pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128',
                        description='Install PyTorch Nightly with CUDA 12.8 support',
                        type='pip',
                        required=True,
                        timeout=900  # Increased timeout for nightly builds
                    ),
                    SetupCommand(
                        command='python -c "import torch; print(f\'PyTorch version: {torch.__version__}\'); print(f\'CUDA available: {torch.cuda.is_available()}\'); print(f\'GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"No GPU\"}\'); print(f\'Test tensor: {torch.rand(1).cuda() if torch.cuda.is_available() else \"No CUDA\"}")',
                        description='Verify PyTorch installation',
                        type='python',
                        required=True
                    )
                ]
            )
            self.profiles['rtx5090'] = rtx5090_profile
        
        # H100 Profile
        if 'h100' not in self.profiles:
            h100_profile = EnvironmentProfile(
                name='h100',
                description='H100 optimized setup',
                gpu_patterns=['h100', 'hopper'],
                cuda_versions=['12.0', '12.1', '12.2'],
                priority=85,
                tags=['pytorch', 'cuda', 'h100'],
                setup_commands=[
                    SetupCommand(
                        command='pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121',
                        description='Install PyTorch with CUDA 12.1 support',
                        type='pip',
                        required=True,
                        timeout=600
                    ),
                    SetupCommand(
                        command='pip install flash-attn --no-build-isolation',
                        description='Install Flash Attention for H100 optimization',
                        type='pip',
                        required=False,
                        timeout=900
                    )
                ]
            )
            self.profiles['h100'] = h100_profile
        
        # A100 Profile
        if 'a100' not in self.profiles:
            a100_profile = EnvironmentProfile(
                name='a100',
                description='A100 optimized setup',
                gpu_patterns=['a100', 'ampere'],
                cuda_versions=['11.8', '12.0', '12.1'],
                priority=80,
                tags=['pytorch', 'cuda', 'a100'],
                setup_commands=[
                    SetupCommand(
                        command='pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118',
                        description='Install PyTorch with CUDA 11.8 support',
                        type='pip',
                        required=True,
                        timeout=600
                    ),
                    SetupCommand(
                        command='pip install transformers accelerate datasets',
                        description='Install common ML packages',
                        type='pip',
                        required=False
                    )
                ]
            )
            self.profiles['a100'] = a100_profile
        
        # Generic ML Profile
        if 'generic_ml' not in self.profiles:
            generic_profile = EnvironmentProfile(
                name='generic_ml',
                description='Generic ML/AI setup for any GPU',
                gpu_patterns=[],  # Matches any GPU
                priority=10,
                tags=['pytorch', 'tensorflow', 'ml'],
                setup_commands=[
                    SetupCommand(
                        command='apt update && apt install -y build-essential git curl zip unzip',
                        description='Install basic development tools',
                        type='apt',
                        required=False
                    ),
                    SetupCommand(
                        command='curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs',
                        description='Install Node.js and npm',
                        type='bash',
                        required=False
                    ),
                    SetupCommand(
                        command='pip install --upgrade pip setuptools wheel',
                        description='Upgrade pip and essential packages',
                        type='pip',
                        required=True
                    ),
                    SetupCommand(
                        command='pip install numpy pandas matplotlib seaborn scikit-learn',
                        description='Install data science basics',
                        type='pip',
                        required=False
                    ),
                    SetupCommand(
                        command='pip install jupyterlab ipywidgets',
                        description='Install Jupyter Lab',
                        type='pip',
                        required=False
                    )
                ]
            )
            self.profiles['generic_ml'] = generic_profile
        
        self._save_profiles()
    
    def detect_and_recommend_profile(self, instance: Instance) -> Tuple[Dict[str, Any], Optional[EnvironmentProfile]]:
        """Detect environment and recommend the best profile"""
        env_info = self.detector.detect_environment(instance)
        
        # Find matching profiles, sorted by priority
        matching_profiles = []
        for profile in self.profiles.values():
            if profile.matches_environment(env_info):
                matching_profiles.append(profile)
        
        # Sort by priority (highest first)
        matching_profiles.sort(key=lambda p: p.priority, reverse=True)
        
        recommended_profile = matching_profiles[0] if matching_profiles else None
        
        return env_info, recommended_profile
    
    def setup_environment(self, instance: Instance, profile_name: Optional[str] = None, 
                         dry_run: bool = False) -> Dict[str, Any]:
        """Setup environment on instance using specified or recommended profile"""
        
        if profile_name:
            if profile_name not in self.profiles:
                return {'success': False, 'error': f"Profile '{profile_name}' not found"}
            profile = self.profiles[profile_name]
            env_info = self.detector.detect_environment(instance)
        else:
            # Auto-detect and recommend
            env_info, profile = self.detect_and_recommend_profile(instance)
            if not profile:
                return {'success': False, 'error': 'No suitable profile found for this environment'}
        
        setup_results = {
            'success': True,
            'profile_used': profile.name,
            'environment_info': env_info,
            'commands_executed': [],
            'failed_commands': [],
            'dry_run': dry_run
        }
        
        if dry_run:
            setup_results['planned_commands'] = [
                {'command': cmd.command, 'description': cmd.description, 'type': cmd.type}
                for cmd in profile.setup_commands
            ]
            return setup_results
        
        # Execute setup commands
        for cmd in profile.setup_commands:
            try:
                success = self._execute_setup_command(instance, cmd)
                
                command_result = {
                    'command': cmd.command,
                    'description': cmd.description,
                    'type': cmd.type,
                    'success': success,
                    'required': cmd.required
                }
                
                if success:
                    setup_results['commands_executed'].append(command_result)
                else:
                    setup_results['failed_commands'].append(command_result)
                    if cmd.required:
                        setup_results['success'] = False
                        break
                        
            except Exception as e:
                command_result = {
                    'command': cmd.command,
                    'description': cmd.description,
                    'error': str(e),
                    'required': cmd.required
                }
                setup_results['failed_commands'].append(command_result)
                if cmd.required:
                    setup_results['success'] = False
                    break
        
        return setup_results
    
    def _execute_setup_command(self, instance: Instance, cmd: SetupCommand) -> bool:
        """Execute a single setup command"""
        for attempt in range(cmd.retry_count):
            try:
                # Check conditions first
                if cmd.conditions:
                    for condition in cmd.conditions:
                        result = self.connection.execute_remote_command(instance, condition)
                        if not result[0]:  # If condition fails
                            return False
                
                # Execute the command
                if cmd.type == 'bash' or cmd.type == 'python':
                    success = self.connection.execute_command(
                        instance, cmd.command, cmd.description
                    )
                elif cmd.type == 'pip':
                    # Normalize pip commands - strip 'pip ' prefix if present, always use python -m pip
                    pip_args = cmd.command
                    if pip_args.startswith('pip '):
                        pip_args = pip_args[4:]  # Remove 'pip ' prefix
                    pip_cmd = f"python -m pip {pip_args}"
                    success = self.connection.execute_command(
                        instance, pip_cmd, cmd.description
                    )
                elif cmd.type == 'apt':
                    # Normalize apt commands - strip apt-get/apt prefix if present, always use apt-get
                    apt_args = cmd.command
                    if apt_args.startswith('sudo '):
                        apt_args = apt_args[5:]  # Remove 'sudo ' prefix
                    if apt_args.startswith('apt-get '):
                        apt_args = apt_args[8:]  # Remove 'apt-get ' prefix
                    elif apt_args.startswith('apt '):
                        apt_args = apt_args[4:]  # Remove 'apt ' prefix
                    apt_cmd = f"apt-get {apt_args}"
                    success = self.connection.execute_command(
                        instance, apt_cmd, cmd.description
                    )
                elif cmd.type == 'conda':
                    success = self.connection.execute_command(
                        instance, cmd.command, cmd.description
                    )
                else:
                    # Generic command execution
                    success = self.connection.execute_command(
                        instance, cmd.command, cmd.description
                    )
                
                if success:
                    return True
                
            except Exception as e:
                if attempt == cmd.retry_count - 1:  # Last attempt
                    raise e
        
        return False
    
    def add_profile(self, profile: EnvironmentProfile) -> bool:
        """Add a new environment profile"""
        try:
            self.profiles[profile.name] = profile
            self._save_profiles()
            return True
        except Exception:
            return False
    
    def remove_profile(self, profile_name: str) -> bool:
        """Remove an environment profile"""
        if profile_name in self.profiles:
            del self.profiles[profile_name]
            self._save_profiles()
            return True
        return False
    
    def list_profiles(self, tag_filter: Optional[str] = None) -> List[EnvironmentProfile]:
        """List all environment profiles, optionally filtered by tag"""
        profiles = list(self.profiles.values())
        
        if tag_filter:
            profiles = [p for p in profiles if tag_filter in p.tags]
        
        # Sort by priority
        profiles.sort(key=lambda p: p.priority, reverse=True)
        return profiles
    
    def get_profile(self, profile_name: str) -> Optional[EnvironmentProfile]:
        """Get a specific profile by name"""
        return self.profiles.get(profile_name)