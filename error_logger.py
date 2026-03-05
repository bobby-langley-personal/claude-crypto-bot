"""
Cloud-based error logging system for the crypto trading bot.
Captures errors, stores them locally, and uploads to cloud storage hourly.
"""
import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from pathlib import Path
import hashlib
import os

class CloudErrorLogger:
    """
    Error logger that captures exceptions, stores them locally,
    and prepares them for cloud sync and GitHub issue creation.
    """
    
    def __init__(self, local_error_file: str = "error_log.json"):
        self.local_error_file = Path(local_error_file)
        self.logger = logging.getLogger(__name__)
        self.errors: List[Dict[str, Any]] = []
        self.load_existing_errors()
        
    def load_existing_errors(self):
        """Load existing errors from local file"""
        if self.local_error_file.exists():
            try:
                with open(self.local_error_file, 'r') as f:
                    self.errors = json.load(f)
            except Exception as e:
                self.logger.warning(f"Could not load existing errors: {e}")
                self.errors = []
    
    def log_error(self, 
                  error: Exception, 
                  context: str = "",
                  severity: str = "error",
                  component: str = "unknown") -> str:
        """
        Log an error with full context and stack trace.
        Returns error ID for tracking.
        """
        error_data = {
            "id": self._generate_error_id(error, context),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context,
            "severity": severity,  # error, warning, critical
            "component": component,  # main, trading_engine, sentiment_analyzer, etc.
            "stack_trace": traceback.format_exc(),
            "resolved": False,
            "github_issue_created": False,
            "github_issue_url": None,
            "fix_attempted": False,
            "fix_description": None
        }
        
        # Check if we already have this error (avoid spam)
        existing_error = self._find_existing_error(error_data["id"])
        if existing_error:
            existing_error["last_occurred"] = error_data["timestamp"]
            existing_error["occurrence_count"] = existing_error.get("occurrence_count", 1) + 1
        else:
            error_data["occurrence_count"] = 1
            error_data["first_occurred"] = error_data["timestamp"]
            error_data["last_occurred"] = error_data["timestamp"]
            self.errors.append(error_data)
        
        self._save_errors()
        self.logger.error(f"Logged error {error_data['id']}: {error_data['error_message']}")
        
        return error_data["id"]
    
    def _generate_error_id(self, error: Exception, context: str) -> str:
        """Generate unique but consistent error ID based on error type and context"""
        content = f"{type(error).__name__}:{str(error)}:{context}"
        return hashlib.md5(content.encode()).hexdigest()[:12]
    
    def _find_existing_error(self, error_id: str) -> Optional[Dict[str, Any]]:
        """Find existing error by ID"""
        for error in self.errors:
            if error["id"] == error_id:
                return error
        return None
    
    def _save_errors(self):
        """Save errors to local file"""
        try:
            with open(self.local_error_file, 'w') as f:
                json.dump(self.errors, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Could not save errors to {self.local_error_file}: {e}")
    
    def get_unresolved_errors(self) -> List[Dict[str, Any]]:
        """Get all unresolved errors"""
        return [error for error in self.errors if not error["resolved"]]
    
    def get_recent_errors(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get errors from the last N hours"""
        cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
        recent_errors = []
        
        for error in self.errors:
            try:
                error_time = datetime.fromisoformat(error["last_occurred"].replace('Z', '+00:00')).timestamp()
                if error_time >= cutoff:
                    recent_errors.append(error)
            except Exception:
                # Include errors with unparseable timestamps to be safe
                recent_errors.append(error)
        
        return recent_errors
    
    def mark_error_resolved(self, error_id: str, fix_description: str = ""):
        """Mark an error as resolved"""
        error = self._find_existing_error(error_id)
        if error:
            error["resolved"] = True
            error["resolved_at"] = datetime.now(timezone.utc).isoformat()
            error["fix_description"] = fix_description
            self._save_errors()
            self.logger.info(f"Marked error {error_id} as resolved")
    
    def mark_github_issue_created(self, error_id: str, issue_url: str):
        """Mark that a GitHub issue has been created for this error"""
        error = self._find_existing_error(error_id)
        if error:
            error["github_issue_created"] = True
            error["github_issue_url"] = issue_url
            error["issue_created_at"] = datetime.now(timezone.utc).isoformat()
            self._save_errors()
            self.logger.info(f"Marked GitHub issue created for error {error_id}: {issue_url}")
    
    def get_error_summary(self) -> Dict[str, Any]:
        """Get summary statistics about errors"""
        total_errors = len(self.errors)
        unresolved_errors = len(self.get_unresolved_errors())
        recent_errors = len(self.get_recent_errors(24))
        critical_errors = len([e for e in self.errors if e["severity"] == "critical"])
        
        return {
            "total_errors": total_errors,
            "unresolved_errors": unresolved_errors,
            "recent_errors_24h": recent_errors,
            "critical_errors": critical_errors,
            "last_error": self.errors[-1] if self.errors else None,
            "components_with_errors": list(set(e["component"] for e in self.errors))
        }
    
    def clear_resolved_errors(self, older_than_days: int = 30):
        """Clear resolved errors older than N days to keep file size manageable"""
        cutoff = datetime.now(timezone.utc).timestamp() - (older_than_days * 24 * 3600)
        
        original_count = len(self.errors)
        self.errors = [
            error for error in self.errors 
            if not error["resolved"] or 
            datetime.fromisoformat(error["resolved_at"].replace('Z', '+00:00')).timestamp() >= cutoff
        ]
        
        if len(self.errors) != original_count:
            self._save_errors()
            self.logger.info(f"Cleared {original_count - len(self.errors)} old resolved errors")


# Global error logger instance
error_logger = CloudErrorLogger()


def log_error(error: Exception, context: str = "", severity: str = "error", component: str = "unknown") -> str:
    """Convenience function to log an error"""
    return error_logger.log_error(error, context, severity, component)


def get_error_summary() -> Dict[str, Any]:
    """Convenience function to get error summary"""
    return error_logger.get_error_summary()