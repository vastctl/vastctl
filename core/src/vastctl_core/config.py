
"""Configuration management for VastCtl"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
from appdirs import user_config_dir, user_data_dir
from dotenv import load_dotenv


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge two dictionaries.

    For nested dicts, recursively merge instead of overwriting.
    This allows users to override individual fields without clobbering
    entire sections (e.g., setting vast.timeout_seconds without losing
    vast.base_url).

    Args:
        base: Base configuration dict
        override: Override values to merge in

    Returns:
        Merged dictionary (base is modified in place and returned)
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            _deep_merge(base[key], value)
        else:
            # Override value directly
            base[key] = value
    return base


class Config:
    """Manage VastCtl configuration"""

    def __init__(self, config_path: Optional[Path] = None):
        # Load environment variables
        load_dotenv()

        # Setup paths
        self.app_name = "vastctl"
        self.config_dir = Path(user_config_dir(self.app_name))
        self.data_dir = Path(user_data_dir(self.app_name))
        
        # Ensure directories exist
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Config file path
        self.config_path = config_path or self.config_dir / "config.yaml"
        
        # Load config
        self._config = self._load_config()
        
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file and environment"""
        config = self._get_defaults()
        
        # Load from file if exists (deep merge to preserve nested defaults)
        if self.config_path.exists():
            with open(self.config_path, 'r') as f:
                file_config = yaml.safe_load(f) or {}
                _deep_merge(config, file_config)
        
        # Override with environment variables
        env_overrides = {
            'api_key': os.getenv('VAST_API_KEY'),
            'ssh_key_path': os.getenv('VAST_SSH_KEY'),
            'default_gpu_type': os.getenv('VAST_GPU_TYPE'),
            'default_disk_gb': os.getenv('VAST_DISK_GB'),
        }

        for key, value in env_overrides.items():
            if value is not None:
                if key == 'default_disk_gb':
                    config[key] = int(value)
                else:
                    config[key] = value

        # Cloud environment overrides (useful for dev/staging)
        cloud_url = os.getenv('VASTLAB_CLOUD_URL')
        if cloud_url:
            config['cloud']['base_url'] = cloud_url

        cloud_enabled = os.getenv('VASTLAB_CLOUD_ENABLED')
        if cloud_enabled is not None:
            config['cloud']['enabled'] = cloud_enabled.lower() in ('true', '1', 'yes')

        return config
    
    def _get_defaults(self) -> Dict[str, Any]:
        """Get default configuration"""
        return {
            'api_key': None,
            'default_gpu_type': 'A100',
            'default_disk_gb': 200,
            'default_template': None,  # e.g., 'ml-training', 'datascience'
            # PyTorch image - has Python, pip, and torch pre-installed
            # Much safer than CUDA base which lacks Python entirely
            'default_image': 'pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime',
            'ssh_key_path': str(Path.home() / '.ssh' / 'vast_rsa'),
            'backup_path': str(self.data_dir / 'backups'),
            'database_path': str(self.data_dir / 'instances.db'),
            'templates_path': str(self.data_dir / 'templates'),

            'defaults': {
                'bandwidth_min': 400,  # Mbps
                'reliability_min': 0.95,
                'price_max': 3.0,  # $/hr
                'jupyter_port': 8888,
                'ssh_timeout': 30,
            },

            'projects': {
                'default': 'default',
                'active': 'default',
            },

            'ui': {
                'color_scheme': 'auto',  # auto, light, dark
                'table_format': 'rich',  # rich, simple, json
                'confirm_stop': True,
                'show_costs': True,
            },

            'transfer': {
                'max_file_size_mb': 40,  # Skip files larger than this in cp -r
                'ignore_large_files': True,  # Enable size-based filtering
                'timeout_seconds': 900,  # Transfer timeout
                'parallel_transfers': True,  # Use parallel transfers by default
                'max_workers': 4,  # Number of parallel workers
                'exclude_patterns': [  # Default exclude patterns
                    '*.tmp',
                    '__pycache__',
                    '.git',
                    'node_modules',
                    '*.log'
                ]
            },

            'vast': {
                'base_url': 'https://console.vast.ai/api/v0',
                'timeout_seconds': 30,
                'poll_interval_seconds': 5,
                'verify_mutations': True,  # Wait for destroy/stop/start to complete
            },

            'ssh': {
                'public_key_path': str(Path.home() / '.ssh' / 'vast_rsa.pub'),
            },

            'provisioning': {
                'mode': 'standard',  # fast|standard|custom
                'pip': {
                    'packages': [
                        'jupyterlab',
                        'notebook',
                        'ipywidgets',
                        'matplotlib',
                        'scipy',
                        'numpy',
                        'pandas',
                        'wandb',
                        'warpdata',
                        'imagecodecs',
                    ],
                    'fast_packages': [
                        'jupyterlab',
                        'notebook',
                    ],
                },
                'torch': {
                    'mode': 'auto',  # skip|auto|cpu|cu124|cu128-nightly
                },
                'apt': {
                    'packages': ['python3', 'python3-pip', 'python-is-python3', 'zip', 'unzip', 'htop', 'tmux'],
                },
                'logging': {
                    'enabled': True,
                    'log_file': '/root/vastlab_onstart.log',
                    'status_file': '/root/.vastlab_setup.json',
                },
            },

            'cloud': {
                'enabled': False,  # Disabled until cloud backend exists
                'base_url': 'https://api.vastlab.dev',
                'timeout_seconds': 20,
                'auto_sync': True,
                'sync_on': {
                    'start': True,
                    'stop': True,
                    'kill': True,
                    'refresh': True,
                },
            },

            # Telemetry (anonymous usage stats) - default OFF for trust
            'telemetry': {
                'enabled': False,
            },

            # Profile cloud cache settings
            'profiles': {
                'cache_path': str(self.data_dir / 'cloud_profiles.json'),
            },

            # Built-in provisioning profiles
            # Use with: vastctl start -n mybox --template ml-training
            'provisioning_profiles': {
                'minimal': {
                    'description': 'Jupyter only, no ML libraries',
                    'pip': {
                        'packages': ['jupyterlab', 'notebook'],
                    },
                    'torch': {'mode': 'skip'},
                    'apt': {'packages': ['python3', 'python3-pip', 'python-is-python3', 'zip', 'unzip']},
                },
                'datascience': {
                    'description': 'Data science stack (pandas, matplotlib, scikit-learn)',
                    'pip': {
                        'packages': [
                            'jupyterlab',
                            'notebook',
                            'ipywidgets',
                            'numpy',
                            'pandas',
                            'matplotlib',
                            'seaborn',
                            'scikit-learn',
                            'scipy',
                        ],
                    },
                    'torch': {'mode': 'auto'},
                    'apt': {'packages': ['python3', 'python3-pip', 'python-is-python3', 'zip', 'unzip', 'htop']},
                },
                'ml-training': {
                    'description': 'Full ML training stack (PyTorch, HuggingFace, W&B)',
                    'pip': {
                        'packages': [
                            'jupyterlab',
                            'notebook',
                            'ipywidgets',
                            'numpy',
                            'pandas',
                            'matplotlib',
                            'scipy',
                            'huggingface_hub',
                            'transformers',
                            'datasets',
                            'accelerate',
                            'wandb',
                            'tensorboard',
                        ],
                    },
                    'torch': {'mode': 'auto'},
                    'apt': {'packages': ['python3', 'python3-pip', 'python-is-python3', 'zip', 'unzip', 'htop', 'tmux', 'git-lfs']},
                },
                'inference': {
                    'description': 'Lightweight inference setup',
                    'pip': {
                        'packages': [
                            'jupyterlab',
                            'transformers',
                            'accelerate',
                            'fastapi',
                            'uvicorn',
                        ],
                    },
                    'torch': {'mode': 'auto'},
                    'apt': {'packages': ['python3', 'python3-pip', 'python-is-python3', 'zip', 'unzip']},
                },
                'llm': {
                    'description': 'LLM development (vLLM, transformers)',
                    'pip': {
                        'packages': [
                            'jupyterlab',
                            'notebook',
                            'transformers',
                            'datasets',
                            'accelerate',
                            'bitsandbytes',
                            'peft',
                            'trl',
                            'wandb',
                        ],
                    },
                    'torch': {'mode': 'auto'},
                    'apt': {'packages': ['python3', 'python3-pip', 'python-is-python3', 'zip', 'unzip', 'htop', 'tmux', 'git-lfs']},
                },
            },
        }
    
    def save(self):
        """Save configuration to file"""
        with open(self.config_path, 'w') as f:
            yaml.dump(self._config, f, default_flow_style=False)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with dot notation support"""
        keys = key.split('.')
        value = self._config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any):
        """Set configuration value with dot notation support"""
        keys = key.split('.')
        config = self._config
        
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        config[keys[-1]] = value
        self.save()
    
    @property
    def api_key(self) -> Optional[str]:
        """Get API key from config, environment, or Vast CLI location"""
        # First check config (which includes env overrides from _load_config)
        key = self.get('api_key')
        
        # If not in config, check environment directly
        if not key:
            key = os.getenv('VAST_API_KEY')
        
        # If still not found, try Vast CLI default location
        if not key:
            vast_key_file = Path.home() / '.config' / 'vastai' / '.vast_api_key'
            if vast_key_file.exists():
                key = vast_key_file.read_text().strip()
        
        return key
    
    @property
    def ssh_key_path(self) -> Path:
        """Get SSH key path"""
        path = Path(self.get('ssh_key_path'))
        
        # Try common locations if default doesn't exist
        if not path.exists():
            for key_name in ['vast_rsa', 'vast_ed25519', 'id_ed25519', 'id_rsa']:
                key_path = Path.home() / '.ssh' / key_name
                if key_path.exists():
                    return key_path
        
        return path
    
    @property
    def database_path(self) -> Path:
        """Get database path"""
        return Path(self.get('database_path'))
    
    @property
    def backup_path(self) -> Path:
        """Get backup path"""
        path = Path(self.get('backup_path'))
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    @property
    def active_project(self) -> str:
        """Get active project"""
        return self.get('projects.active', 'default')
    
    def set_active_project(self, project: str):
        """Set active project"""
        self.set('projects.active', project)

    @property
    def default_env_path(self) -> Path:
        """Get path to the global default .vastenv file"""
        return self.config_dir / ".vastenv"
    
    @property
    def max_file_size_mb(self) -> int:
        """Get maximum file size for transfers in MB"""
        value = self.get('transfer.max_file_size_mb', 40)
        return int(value) if value is not None else 40
    
    @property
    def ignore_large_files(self) -> bool:
        """Whether to ignore large files during transfers"""
        return self.get('transfer.ignore_large_files', True)
    
    @property
    def transfer_exclude_patterns(self) -> list:
        """Get file patterns to exclude from transfers"""
        return self.get('transfer.exclude_patterns', [])
    
    def set_max_file_size(self, size_mb: int):
        """Set maximum file size for transfers"""
        self.set('transfer.max_file_size_mb', size_mb)
    
    def set_ignore_large_files(self, ignore: bool):
        """Set whether to ignore large files"""
        self.set('transfer.ignore_large_files', ignore)
    
    @property
    def parallel_transfers(self) -> bool:
        """Whether to use parallel transfers by default"""
        return self.get('transfer.parallel_transfers', True)
    
    @property
    def max_transfer_workers(self) -> int:
        """Default number of parallel workers"""
        value = self.get('transfer.max_workers', 4)
        return int(value) if value is not None else 4

    # Vast API settings
    @property
    def vast_base_url(self) -> str:
        """Get Vast.ai API base URL"""
        return self.get('vast.base_url', 'https://console.vast.ai/api/v0')

    @property
    def vast_timeout_seconds(self) -> int:
        """Get Vast.ai API timeout in seconds"""
        return int(self.get('vast.timeout_seconds', 30))

    @property
    def vast_poll_interval_seconds(self) -> int:
        """Get polling interval for status checks"""
        return int(self.get('vast.poll_interval_seconds', 5))

    @property
    def verify_mutations(self) -> bool:
        """Whether to verify mutations (destroy/stop/start) complete"""
        return bool(self.get('vast.verify_mutations', True))

    @property
    def ssh_public_key_path(self) -> Path:
        """Get SSH public key path for attaching to instances"""
        path = Path(self.get('ssh.public_key_path', str(Path.home() / '.ssh' / 'vast_rsa.pub')))

        # Try common locations if default doesn't exist
        if not path.exists():
            for key_name in ['vast_rsa.pub', 'vast_ed25519.pub', 'id_ed25519.pub', 'id_rsa.pub']:
                key_path = Path.home() / '.ssh' / key_name
                if key_path.exists():
                    return key_path

        return path

    # Cloud settings
    @property
    def cloud_enabled(self) -> bool:
        """Whether cloud features are enabled"""
        return bool(self.get('cloud.enabled', False))

    @property
    def cloud_base_url(self) -> str:
        """Get VastLab Cloud API base URL"""
        return self.get('cloud.base_url', 'https://api.vastlab.ai')

    @property
    def cloud_timeout_seconds(self) -> int:
        """Get VastLab Cloud API timeout in seconds"""
        return int(self.get('cloud.timeout_seconds', 20))

    @property
    def cloud_auto_sync(self) -> bool:
        """Whether to auto-sync after state changes"""
        return bool(self.get('cloud.auto_sync', True))

    @property
    def cloud_token_file(self) -> Path:
        """Get path to cloud token file (fallback storage)"""
        return self.config_dir / "cloud_token"

    def cloud_sync_on(self, action: str) -> bool:
        """Check if auto-sync is enabled for a specific action.

        Args:
            action: One of 'start', 'stop', 'kill', 'refresh'

        Returns:
            True if sync should happen after this action
        """
        if not self.cloud_auto_sync:
            return False
        return bool(self.get(f'cloud.sync_on.{action}', True))

    # Telemetry settings
    @property
    def telemetry_enabled(self) -> bool:
        """Whether anonymous telemetry is enabled"""
        return bool(self.get('telemetry.enabled', False))

    # Profiles settings
    @property
    def profiles_cache_path(self) -> Path:
        """Get path to cloud profiles cache file"""
        return Path(self.get('profiles.cache_path', str(self.data_dir / 'cloud_profiles.json')))
