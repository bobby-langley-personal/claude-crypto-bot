"""
Health check scheduler that runs periodic health checks and manages error logging.
Runs health checks every hour and uploads error logs to cloud storage.
"""
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from health_checker import health_checker
from error_logger import error_logger


class HealthScheduler:
    """
    Schedules and manages health checks and error log uploads.
    """
    
    def __init__(self, check_interval_minutes: int = 60):
        self.check_interval_minutes = check_interval_minutes
        self.logger = logging.getLogger(__name__)
        self.running = False
        self.thread: Optional[threading.Thread] = None
        
    def start(self):
        """Start the health check scheduler"""
        if self.running:
            self.logger.warning("Health scheduler is already running")
            return
        
        self.running = True
        self.thread = threading.Thread(
            target=self._health_check_loop,
            daemon=True,
            name="HealthScheduler"
        )
        self.thread.start()
        self.logger.info(f"Health scheduler started (checks every {self.check_interval_minutes} minutes)")
    
    def stop(self):
        """Stop the health check scheduler"""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        self.logger.info("Health scheduler stopped")
    
    def _health_check_loop(self):
        """Main health check loop"""
        while self.running:
            try:
                # Run health check
                health_data = health_checker.run_health_check()
                
                # Log health status
                if health_data["status"] == "critical":
                    self.logger.error(f"Health check CRITICAL: {len(health_data['issues'])} issues found")
                elif health_data["status"] == "warning":
                    self.logger.warning(f"Health check WARNING: {len(health_data['issues'])} issues found")
                else:
                    self.logger.info("Health check passed: system is healthy")
                
                # Clean up old resolved errors periodically
                error_logger.clear_resolved_errors(older_than_days=30)
                
            except Exception as e:
                # Log error in health checking itself
                self.logger.error(f"Error during health check: {e}")
                error_logger.log_error(e, "health check execution", "error", "health_scheduler")
            
            # Sleep in small chunks to allow for responsive shutdown
            sleep_seconds = self.check_interval_minutes * 60
            for _ in range(sleep_seconds // 5):
                if not self.running:
                    break
                time.sleep(5)
    
    def run_immediate_check(self) -> dict:
        """Run an immediate health check and return results"""
        try:
            return health_checker.run_health_check()
        except Exception as e:
            self.logger.error(f"Error during immediate health check: {e}")
            error_logger.log_error(e, "immediate health check", "error", "health_scheduler")
            return {"status": "error", "error": str(e)}


# Global health scheduler instance
health_scheduler = HealthScheduler(check_interval_minutes=60)