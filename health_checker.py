"""
Health check system for the crypto trading bot.
Monitors system health, analyzes errors, and creates GitHub issues for problems.
"""
import json
import logging
import os
import subprocess
import requests
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from pathlib import Path

from error_logger import error_logger


class HealthChecker:
    """
    Monitors system health and creates GitHub issues for problems.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.github_token = os.getenv("GITHUB_TOKEN", "")
        self.github_repo = os.getenv("GITHUB_REPO", "bobby-langley-personal/claude-crypto-bot")
        self.health_log_file = Path("health_check.json")
        self.last_health_check = self._load_last_health_check()
        
    def _load_last_health_check(self) -> Optional[datetime]:
        """Load timestamp of last health check"""
        if self.health_log_file.exists():
            try:
                with open(self.health_log_file, 'r') as f:
                    data = json.load(f)
                    return datetime.fromisoformat(data.get("last_check", "").replace('Z', '+00:00'))
            except Exception as e:
                self.logger.warning(f"Could not load health check data: {e}")
        return None
    
    def _save_health_check(self, health_data: Dict[str, Any]):
        """Save health check results"""
        try:
            with open(self.health_log_file, 'w') as f:
                json.dump(health_data, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Could not save health check data: {e}")
    
    def run_health_check(self) -> Dict[str, Any]:
        """
        Run comprehensive health check and return results.
        """
        self.logger.info("Running health check...")
        
        health_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "healthy",
            "issues": [],
            "error_summary": error_logger.get_error_summary(),
            "system_checks": self._run_system_checks(),
            "bot_checks": self._run_bot_checks()
        }
        
        # Analyze errors and determine if GitHub issues needed
        critical_issues = self._analyze_errors()
        if critical_issues:
            health_data["status"] = "critical"
            health_data["issues"].extend(critical_issues)
            
            # Create GitHub issues for critical problems
            for issue in critical_issues:
                if issue.get("create_github_issue", False):
                    self._create_github_issue(issue)
        
        # Check for recurring problems
        recurring_issues = self._check_recurring_issues()
        if recurring_issues:
            health_data["issues"].extend(recurring_issues)
            if health_data["status"] == "healthy":
                health_data["status"] = "warning"
        
        self._save_health_check(health_data)
        self.logger.info(f"Health check completed. Status: {health_data['status']}")
        
        return health_data
    
    def _run_system_checks(self) -> Dict[str, Any]:
        """Run basic system health checks"""
        checks = {
            "disk_space": self._check_disk_space(),
            "memory_usage": self._check_memory(),
            "log_file_size": self._check_log_file_size(),
            "required_files": self._check_required_files()
        }
        return checks
    
    def _run_bot_checks(self) -> Dict[str, Any]:
        """Run bot-specific health checks"""
        checks = {
            "config_valid": self._check_config(),
            "api_keys_present": self._check_api_keys(),
            "portfolio_file": self._check_portfolio_file(),
            "recent_activity": self._check_recent_activity()
        }
        return checks
    
    def _check_disk_space(self) -> Dict[str, Any]:
        """Check available disk space"""
        try:
            result = subprocess.run(['df', '-h', '.'], capture_output=True, text=True)
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                parts = lines[1].split()
                return {
                    "status": "ok",
                    "available": parts[3] if len(parts) > 3 else "unknown",
                    "used_percent": parts[4] if len(parts) > 4 else "unknown"
                }
        except Exception as e:
            return {"status": "error", "error": str(e)}
        
        return {"status": "unknown"}
    
    def _check_memory(self) -> Dict[str, Any]:
        """Check memory usage"""
        try:
            with open('/proc/meminfo', 'r') as f:
                lines = f.readlines()
                mem_info = {}
                for line in lines[:3]:  # MemTotal, MemFree, MemAvailable
                    if ':' in line:
                        key, value = line.split(':', 1)
                        mem_info[key.strip()] = value.strip()
                
                return {"status": "ok", "memory_info": mem_info}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def _check_log_file_size(self) -> Dict[str, Any]:
        """Check if log file is getting too large"""
        try:
            log_file = Path("bot.log")
            if log_file.exists():
                size_mb = log_file.stat().st_size / (1024 * 1024)
                status = "ok" if size_mb < 100 else "warning"  # Warning if > 100MB
                return {"status": status, "size_mb": round(size_mb, 2)}
            else:
                return {"status": "warning", "message": "Log file does not exist"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def _check_required_files(self) -> Dict[str, Any]:
        """Check that required files exist"""
        required_files = [
            "config.py",
            "main.py", 
            "trading_engine.py",
            "requirements.txt"
        ]
        
        missing_files = []
        for file in required_files:
            if not Path(file).exists():
                missing_files.append(file)
        
        status = "ok" if not missing_files else "error"
        return {"status": status, "missing_files": missing_files}
    
    def _check_config(self) -> Dict[str, Any]:
        """Check if configuration is valid"""
        try:
            import config
            # Basic config validation
            if not hasattr(config, 'COINS') or not config.COINS:
                return {"status": "error", "message": "No coins configured"}
            
            if not hasattr(config, 'ANTHROPIC_API_KEY') or not config.ANTHROPIC_API_KEY:
                return {"status": "warning", "message": "Anthropic API key not set"}
            
            return {"status": "ok", "coins_count": len(config.COINS)}
        except ImportError as e:
            return {"status": "error", "error": f"Could not import config: {e}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def _check_api_keys(self) -> Dict[str, Any]:
        """Check if required API keys are present"""
        keys_status = {}
        
        api_keys = [
            ("ANTHROPIC_API_KEY", "required"),
            ("CRYPTOPANIC_API_KEY", "optional"),
            ("GITHUB_TOKEN", "optional")
        ]
        
        for key, requirement in api_keys:
            value = os.getenv(key, "")
            keys_status[key] = {
                "present": bool(value),
                "required": requirement == "required"
            }
        
        missing_required = [k for k, v in keys_status.items() if v["required"] and not v["present"]]
        status = "ok" if not missing_required else "error"
        
        return {
            "status": status,
            "keys": keys_status,
            "missing_required": missing_required
        }
    
    def _check_portfolio_file(self) -> Dict[str, Any]:
        """Check portfolio file health"""
        try:
            portfolio_file = Path("portfolio.json")
            if not portfolio_file.exists():
                return {"status": "warning", "message": "Portfolio file does not exist"}
            
            with open(portfolio_file, 'r') as f:
                portfolio_data = json.load(f)
            
            return {
                "status": "ok", 
                "positions_count": len(portfolio_data.get("positions", [])),
                "cash": portfolio_data.get("cash", 0)
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def _check_recent_activity(self) -> Dict[str, Any]:
        """Check for recent bot activity"""
        try:
            log_file = Path("bot.log")
            if not log_file.exists():
                return {"status": "warning", "message": "No log file found"}
            
            # Check last modification time
            last_modified = datetime.fromtimestamp(log_file.stat().st_mtime, tz=timezone.utc)
            hours_since_activity = (datetime.now(timezone.utc) - last_modified).total_seconds() / 3600
            
            status = "ok" if hours_since_activity < 2 else "warning"
            return {
                "status": status,
                "hours_since_activity": round(hours_since_activity, 1),
                "last_activity": last_modified.isoformat()
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def _analyze_errors(self) -> List[Dict[str, Any]]:
        """Analyze errors and determine which need GitHub issues"""
        critical_issues = []
        
        # Check for critical unresolved errors
        unresolved_errors = error_logger.get_unresolved_errors()
        critical_errors = [e for e in unresolved_errors if e["severity"] == "critical"]
        
        for error in critical_errors:
            if not error["github_issue_created"]:
                critical_issues.append({
                    "type": "critical_error",
                    "error_id": error["id"],
                    "title": f"Critical Error: {error['error_type']} in {error['component']}",
                    "description": self._format_error_for_issue(error),
                    "create_github_issue": True,
                    "labels": ["claude", "bug", "critical"]
                })
        
        # Check for high-frequency errors (same error occurring many times)
        for error in unresolved_errors:
            if error["occurrence_count"] > 10 and not error["github_issue_created"]:
                critical_issues.append({
                    "type": "high_frequency_error",
                    "error_id": error["id"],
                    "title": f"High Frequency Error: {error['error_type']} ({error['occurrence_count']} times)",
                    "description": self._format_error_for_issue(error),
                    "create_github_issue": True,
                    "labels": ["claude", "bug", "high-frequency"]
                })
        
        return critical_issues
    
    def _check_recurring_issues(self) -> List[Dict[str, Any]]:
        """Check for recurring issues that need attention"""
        issues = []
        
        # Check error trends
        recent_errors = error_logger.get_recent_errors(24)
        if len(recent_errors) > 20:  # More than 20 errors in 24 hours
            issues.append({
                "type": "high_error_rate",
                "title": f"High error rate detected ({len(recent_errors)} errors in 24h)",
                "severity": "warning"
            })
        
        return issues
    
    def _format_error_for_issue(self, error: Dict[str, Any]) -> str:
        """Format error data for GitHub issue description"""
        description = f"""
## Error Details

**Error ID:** `{error['id']}`
**Type:** {error['error_type']}  
**Component:** {error['component']}
**Severity:** {error['severity']}
**First Occurred:** {error['first_occurred']}
**Last Occurred:** {error['last_occurred']}
**Occurrence Count:** {error['occurrence_count']}

## Error Message
```
{error['error_message']}
```

## Context
{error['context']}

## Stack Trace
```
{error['stack_trace']}
```

## Instructions for Claude
This error has been automatically detected by the health check system. Please:

1. Analyze the error and its context
2. Identify the root cause  
3. Implement a fix
4. Test the fix thoroughly
5. Update this issue with the resolution

If manual intervention is needed, please provide detailed instructions for the maintainer.

---
*This issue was created automatically by the health check system.*
        """.strip()
        
        return description
    
    def _create_github_issue(self, issue: Dict[str, Any]) -> bool:
        """Create a GitHub issue for the given problem"""
        if not self.github_token:
            self.logger.warning("No GitHub token available, cannot create issue")
            return False
        
        try:
            url = f"https://api.github.com/repos/{self.github_repo}/issues"
            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            data = {
                "title": issue["title"],
                "body": issue["description"],
                "labels": issue.get("labels", ["claude", "bug"])
            }
            
            response = requests.post(url, headers=headers, json=data)
            
            if response.status_code == 201:
                issue_data = response.json()
                issue_url = issue_data["html_url"]
                self.logger.info(f"Created GitHub issue: {issue_url}")
                
                # Mark the error as having a GitHub issue created
                if "error_id" in issue:
                    error_logger.mark_github_issue_created(issue["error_id"], issue_url)
                
                return True
            else:
                self.logger.error(f"Failed to create GitHub issue: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error creating GitHub issue: {e}")
            return False


# Global health checker instance
health_checker = HealthChecker()