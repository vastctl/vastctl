"""Instance model and operations for VastLab"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path
import subprocess
import time


@dataclass
class Instance:
    """Represents a Vast.ai GPU instance"""
    
    # Identity
    name: str
    vast_id: Optional[int] = None
    machine_id: Optional[int] = None
    
    # Configuration
    gpu_type: str = "A100"
    gpu_count: int = 1
    disk_gb: int = 200
    image: str = "pytorch/pytorch:2.2.2-cuda12.1-cudnn8-runtime"
    
    # Project/organization
    project: str = "default"
    description: str = ""
    tags: List[str] = field(default_factory=list)
    
    # Connection info
    ssh_host: Optional[str] = None
    ssh_port: Optional[int] = None
    jupyter_token: Optional[str] = None
    jupyter_port: int = 8888
    
    # Resource info
    price_per_hour: float = 0.0
    bandwidth_mbps: Optional[float] = None
    storage_path: Optional[str] = None
    reliability: float = 0.0
    
    # Status
    status: str = "stopped"  # stopped, starting, running, stopping
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    last_accessed: Optional[datetime] = None
    
    # Tracking
    total_runtime_hours: float = 0.0
    total_cost: float = 0.0
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate and process initialization"""
        if not self.name:
            raise ValueError("Instance name is required")
        
        # Sanitize name
        self.name = self.name.lower().replace(' ', '-')
    
    @property
    def is_running(self) -> bool:
        """Check if instance is running"""
        return self.status == "running"
    
    @property
    def connection_string(self) -> Optional[str]:
        """Get SSH connection string"""
        if self.ssh_host and self.ssh_port:
            return f"{self.ssh_host}:{self.ssh_port}"
        return None
    
    @property
    def jupyter_url(self) -> Optional[str]:
        """Get Jupyter URL"""
        if self.jupyter_token:
            return f"http://localhost:{self.jupyter_port}/lab?token={self.jupyter_token}"
        return None
    
    @property
    def runtime_hours(self) -> float:
        """Calculate current runtime in hours"""
        if self.started_at and self.is_running:
            delta = datetime.now() - self.started_at
            return delta.total_seconds() / 3600
        return self.total_runtime_hours
    
    @property
    def current_cost(self) -> float:
        """Calculate current cost"""
        if self.is_running and self.started_at:
            runtime = self.runtime_hours - self.total_runtime_hours
            return self.total_cost + (runtime * self.price_per_hour)
        return self.total_cost
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            'name': self.name,
            'vast_id': self.vast_id,
            'machine_id': self.machine_id,
            'gpu_type': self.gpu_type,
            'gpu_count': self.gpu_count,
            'disk_gb': self.disk_gb,
            'image': self.image,
            'project': self.project,
            'description': self.description,
            'tags': self.tags,
            'ssh_host': self.ssh_host,
            'ssh_port': self.ssh_port,
            'jupyter_token': self.jupyter_token,
            'jupyter_port': self.jupyter_port,
            'price_per_hour': self.price_per_hour,
            'bandwidth_mbps': self.bandwidth_mbps,
            'storage_path': self.storage_path,
            'reliability': self.reliability,
            'status': self.status,
            'created_at': self.created_at.isoformat(),
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'last_accessed': self.last_accessed.isoformat() if self.last_accessed else None,
            'total_runtime_hours': self.total_runtime_hours,
            'total_cost': self.total_cost,
            'metadata': self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Instance':
        """Create instance from dictionary"""
        # Parse datetime fields
        for field in ['created_at', 'started_at', 'last_accessed']:
            if data.get(field):
                data[field] = datetime.fromisoformat(data[field])
        
        return cls(**data)
    
    def update_status(self, status: str):
        """Update instance status"""
        old_status = self.status
        self.status = status
        
        # Update timestamps
        if status == "running" and old_status != "running":
            self.started_at = datetime.now()
        elif status == "stopped" and old_status == "running":
            if self.started_at:
                runtime = (datetime.now() - self.started_at).total_seconds() / 3600
                self.total_runtime_hours += runtime
                self.total_cost += runtime * self.price_per_hour
            self.started_at = None
    
    def mark_accessed(self):
        """Mark instance as accessed"""
        self.last_accessed = datetime.now()
    
    def add_tag(self, tag: str):
        """Add a tag to the instance"""
        if tag not in self.tags:
            self.tags.append(tag)
    
    def remove_tag(self, tag: str):
        """Remove a tag from the instance"""
        if tag in self.tags:
            self.tags.remove(tag)
    
    def matches_filter(self, 
                      project: Optional[str] = None,
                      status: Optional[str] = None,
                      tags: Optional[List[str]] = None) -> bool:
        """Check if instance matches filter criteria"""
        if project and self.project != project:
            return False
        if status and self.status != status:
            return False
        if tags and not any(tag in self.tags for tag in tags):
            return False
        return True