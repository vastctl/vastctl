"""Instance registry for VastLab"""

import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

from .instance import Instance
from .config import Config


logger = logging.getLogger(__name__)


class Registry:
    """Manage instance registry with SQLite backend"""
    
    def __init__(self, config: Config):
        self.config = config
        self.db_path = config.database_path
        self._init_database()
        self._active_instance: Optional[str] = self._load_active_instance()
    
    def _init_database(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS instances (
                    name TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_instances_created 
                ON instances(created_at)
            """)
            
            conn.commit()
    
    def add(self, instance: Instance) -> None:
        """Add or update instance in registry"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO instances (name, data, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (instance.name, json.dumps(instance.to_dict())))
            conn.commit()
        
        logger.info(f"Added/updated instance: {instance.name}")
    
    def get(self, name: str) -> Optional[Instance]:
        """Get instance by name"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT data FROM instances WHERE name = ?", 
                (name,)
            )
            row = cursor.fetchone()
            
            if row:
                data = json.loads(row[0])
                return Instance.from_dict(data)
        
        return None
    
    def list(self, 
             project: Optional[str] = None,
             status: Optional[str] = None,
             tags: Optional[List[str]] = None) -> List[Instance]:
        """List instances with optional filters"""
        instances = []
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT data FROM instances ORDER BY created_at DESC"
            )
            
            for row in cursor:
                data = json.loads(row[0])
                instance = Instance.from_dict(data)
                
                # Apply filters
                if instance.matches_filter(project, status, tags):
                    instances.append(instance)
        
        return instances
    
    def remove(self, name: str) -> bool:
        """Remove instance from registry"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM instances WHERE name = ?",
                (name,)
            )
            conn.commit()
            
            if cursor.rowcount > 0:
                logger.info(f"Removed instance: {name}")
                
                # Clear active instance if it was removed
                if self._active_instance == name:
                    self._active_instance = None
                    self._save_active_instance()
                
                return True
        
        return False
    
    def update(self, name: str, updates: Dict[str, Any]) -> bool:
        """Update instance fields"""
        instance = self.get(name)
        if not instance:
            return False
        
        # Apply updates
        for key, value in updates.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        
        # Save back
        self.add(instance)
        return True
    
    def exists(self, name: str) -> bool:
        """Check if instance exists"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT 1 FROM instances WHERE name = ? LIMIT 1",
                (name,)
            )
            return cursor.fetchone() is not None
    
    @property
    def active_instance(self) -> Optional[str]:
        """Get active instance name"""
        return self._active_instance
    
    def set_active(self, name: str) -> bool:
        """Set active instance"""
        if self.exists(name):
            self._active_instance = name
            self._save_active_instance()
            return True
        return False
    
    def get_active(self) -> Optional[Instance]:
        """Get active instance object"""
        if self._active_instance:
            return self.get(self._active_instance)
        return None
    
    def _load_active_instance(self) -> Optional[str]:
        """Load active instance from database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT value FROM settings WHERE key = 'active_instance'"
            )
            row = cursor.fetchone()
            return row[0] if row else None
    
    def _save_active_instance(self):
        """Save active instance to database"""
        with sqlite3.connect(self.db_path) as conn:
            if self._active_instance:
                conn.execute("""
                    INSERT OR REPLACE INTO settings (key, value)
                    VALUES ('active_instance', ?)
                """, (self._active_instance,))
            else:
                conn.execute(
                    "DELETE FROM settings WHERE key = 'active_instance'"
                )
            conn.commit()
    
    def get_projects(self) -> List[str]:
        """Get list of all projects"""
        projects = set()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT data FROM instances")
            
            for row in cursor:
                data = json.loads(row[0])
                projects.add(data.get('project', 'default'))
        
        return sorted(list(projects))
    
    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics"""
        instances = self.list()
        
        stats = {
            'total_instances': len(instances),
            'running_instances': sum(1 for i in instances if i.is_running),
            'total_cost': sum(i.current_cost for i in instances),
            'total_runtime_hours': sum(i.runtime_hours for i in instances),
            'projects': self.get_projects(),
            'by_gpu_type': {},
            'by_project': {},
        }
        
        # Group by GPU type
        for instance in instances:
            gpu_key = f"{instance.gpu_count}x{instance.gpu_type}"
            if gpu_key not in stats['by_gpu_type']:
                stats['by_gpu_type'][gpu_key] = 0
            stats['by_gpu_type'][gpu_key] += 1
        
        # Group by project
        for instance in instances:
            if instance.project not in stats['by_project']:
                stats['by_project'][instance.project] = {
                    'count': 0,
                    'cost': 0,
                    'runtime': 0,
                }
            stats['by_project'][instance.project]['count'] += 1
            stats['by_project'][instance.project]['cost'] += instance.current_cost
            stats['by_project'][instance.project]['runtime'] += instance.runtime_hours
        
        return stats