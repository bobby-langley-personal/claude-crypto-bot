"""
Automated fix system for the crypto trading bot.
Analyzes errors and attempts to implement fixes automatically via Claude Code.
"""
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from pathlib import Path

from error_logger import error_logger


class AutoFixer:
    """
    Automatically attempts to fix common errors and creates branches for fixes.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.auto_fix_enabled = os.getenv("AUTO_FIX_ENABLED", "true").lower() == "true"
        self.github_token = os.getenv("GITHUB_TOKEN", "")
        self.github_repo = os.getenv("GITHUB_REPO", "bobby-langley-personal/claude-crypto-bot")
        
    def analyze_and_fix_errors(self) -> Dict[str, Any]:
        """
        Analyze unresolved errors and attempt automatic fixes.
        """
        if not self.auto_fix_enabled:
            self.logger.info("Automatic fixing is disabled")
            return {"status": "disabled"}
        
        self.logger.info("Starting automated error analysis and fixing...")
        
        unresolved_errors = error_logger.get_unresolved_errors()
        fix_results = {
            "analyzed_errors": len(unresolved_errors),
            "fixable_errors": [],
            "fixes_attempted": [],
            "fixes_successful": [],
            "manual_intervention_required": []
        }
        
        for error in unresolved_errors:
            if error.get("fix_attempted", False):
                continue  # Skip errors we've already tried to fix
                
            fix_strategy = self._analyze_error_for_fix(error)
            if fix_strategy:
                fix_results["fixable_errors"].append(error["id"])
                
                if fix_strategy["confidence"] >= 0.8:  # High confidence fixes only
                    success = self._attempt_fix(error, fix_strategy)
                    fix_results["fixes_attempted"].append({
                        "error_id": error["id"],
                        "strategy": fix_strategy["type"],
                        "success": success
                    })
                    
                    if success:
                        fix_results["fixes_successful"].append(error["id"])
                else:
                    fix_results["manual_intervention_required"].append({
                        "error_id": error["id"],
                        "reason": "Low confidence in automatic fix",
                        "suggested_fix": fix_strategy["description"]
                    })
        
        return fix_results
    
    def _analyze_error_for_fix(self, error: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Analyze an error to determine if it can be automatically fixed.
        """
        error_type = error["error_type"]
        error_message = error["error_message"]
        component = error["component"]
        
        # Import errors
        if "ImportError" in error_type or "ModuleNotFoundError" in error_type:
            return {
                "type": "missing_import",
                "confidence": 0.9,
                "description": "Add missing import statement",
                "action": "add_import",
                "module": self._extract_missing_module(error_message)
            }
        
        # NameError for undefined variables
        if "NameError" in error_type and "not defined" in error_message:
            return {
                "type": "undefined_variable",
                "confidence": 0.7,
                "description": "Fix undefined variable reference",
                "action": "fix_variable",
                "variable": self._extract_undefined_variable(error_message)
            }
        
        # AttributeError for missing methods/attributes
        if "AttributeError" in error_type:
            return {
                "type": "missing_attribute",
                "confidence": 0.6,
                "description": "Fix missing attribute or method call",
                "action": "fix_attribute",
                "details": error_message
            }
        
        # Syntax errors
        if "SyntaxError" in error_type:
            return {
                "type": "syntax_error",
                "confidence": 0.8,
                "description": "Fix syntax error",
                "action": "fix_syntax",
                "details": error_message
            }
        
        # File not found errors
        if "FileNotFoundError" in error_type:
            return {
                "type": "missing_file",
                "confidence": 0.5,
                "description": "Create missing file or fix path",
                "action": "fix_file_path",
                "file_path": self._extract_missing_file(error_message)
            }
        
        return None
    
    def _extract_missing_module(self, error_message: str) -> str:
        """Extract module name from ImportError message"""
        if "No module named" in error_message:
            # Extract module name from "No module named 'module_name'"
            start = error_message.find("'") + 1
            end = error_message.find("'", start)
            return error_message[start:end] if start > 0 and end > start else ""
        return ""
    
    def _extract_undefined_variable(self, error_message: str) -> str:
        """Extract variable name from NameError message"""
        if "name" in error_message and "is not defined" in error_message:
            # Extract from "name 'variable_name' is not defined"
            start = error_message.find("'") + 1
            end = error_message.find("'", start)
            return error_message[start:end] if start > 0 and end > start else ""
        return ""
    
    def _extract_missing_file(self, error_message: str) -> str:
        """Extract file path from FileNotFoundError message"""
        # Try to extract file path from error message
        if "No such file or directory:" in error_message:
            return error_message.split(":", 1)[1].strip().strip("'\"")
        return ""
    
    def _attempt_fix(self, error: Dict[str, Any], fix_strategy: Dict[str, Any]) -> bool:
        """
        Attempt to automatically fix the error.
        """
        try:
            # Create a new branch for the fix
            branch_name = f"autofix/{error['id']}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"
            
            if not self._create_fix_branch(branch_name):
                return False
            
            # Apply the fix based on strategy
            fix_applied = False
            if fix_strategy["action"] == "add_import":
                fix_applied = self._fix_missing_import(error, fix_strategy)
            elif fix_strategy["action"] == "fix_variable":
                fix_applied = self._fix_undefined_variable(error, fix_strategy)
            elif fix_strategy["action"] == "fix_attribute":
                fix_applied = self._fix_missing_attribute(error, fix_strategy)
            elif fix_strategy["action"] == "fix_syntax":
                fix_applied = self._fix_syntax_error(error, fix_strategy)
            elif fix_strategy["action"] == "fix_file_path":
                fix_applied = self._fix_missing_file(error, fix_strategy)
            
            if fix_applied:
                # Commit the fix
                commit_message = f"Auto-fix: {error['error_type']} in {error['component']}\n\nError ID: {error['id']}\nFix: {fix_strategy['description']}"
                self._commit_fix(commit_message)
                
                # Mark error as fix attempted
                error_logger.log_error_fix_attempted(error["id"], fix_strategy["description"], True)
                
                self.logger.info(f"Successfully applied fix for error {error['id']}")
                return True
            else:
                # Mark error as fix attempted but failed
                error_logger.log_error_fix_attempted(error["id"], fix_strategy["description"], False)
                return False
                
        except Exception as e:
            self.logger.error(f"Error while attempting fix for {error['id']}: {e}")
            error_logger.log_error(e, f"auto-fix attempt for error {error['id']}", "error", "auto_fixer")
            return False
    
    def _create_fix_branch(self, branch_name: str) -> bool:
        """Create a new branch for the fix"""
        try:
            # Make sure we're on main and up to date
            subprocess.run(["git", "checkout", "main"], check=True, capture_output=True)
            subprocess.run(["git", "pull", "origin", "main"], check=True, capture_output=True)
            
            # Create new branch
            subprocess.run(["git", "checkout", "-b", branch_name], check=True, capture_output=True)
            
            self.logger.info(f"Created fix branch: {branch_name}")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to create fix branch: {e}")
            return False
    
    def _fix_missing_import(self, error: Dict[str, Any], fix_strategy: Dict[str, Any]) -> bool:
        """Fix missing import errors"""
        # This is a simplified implementation
        # In practice, you'd want more sophisticated logic to determine the correct import
        module = fix_strategy["module"]
        
        if module == "timedelta":
            # Special case for the timedelta error
            return self._add_import_to_file(error["component"], "from datetime import timedelta")
        
        # Generic import fixes would go here
        return False
    
    def _fix_undefined_variable(self, error: Dict[str, Any], fix_strategy: Dict[str, Any]) -> bool:
        """Fix undefined variable errors"""
        variable = fix_strategy["variable"]
        
        if variable.lower() == "timedelta":
            # Fix the specific timedelta error
            return self._add_import_to_file(error["component"], "from datetime import timedelta")
        
        return False
    
    def _fix_missing_attribute(self, error: Dict[str, Any], fix_strategy: Dict[str, Any]) -> bool:
        """Fix missing attribute errors"""
        # This would require more complex analysis
        return False
    
    def _fix_syntax_error(self, error: Dict[str, Any], fix_strategy: Dict[str, Any]) -> bool:
        """Fix syntax errors"""
        # This would require parsing and fixing syntax
        return False
    
    def _fix_missing_file(self, error: Dict[str, Any], fix_strategy: Dict[str, Any]) -> bool:
        """Fix missing file errors"""
        # This would involve creating files or fixing paths
        return False
    
    def _add_import_to_file(self, component: str, import_statement: str) -> bool:
        """Add an import statement to a Python file"""
        try:
            # Map component names to file paths
            component_files = {
                "main": "main.py",
                "trading_engine": "trading_engine.py",
                "sentiment_analyzer": "sentiment_analyzer.py",
                "bot_controller": "bot_controller.py"
            }
            
            file_path = component_files.get(component)
            if not file_path or not Path(file_path).exists():
                return False
            
            # Read the file
            with open(file_path, 'r') as f:
                lines = f.readlines()
            
            # Check if import already exists
            if any(import_statement.strip() in line for line in lines):
                return True  # Already imported
            
            # Find the right place to add the import (after existing imports)
            insert_index = 0
            for i, line in enumerate(lines):
                if line.strip().startswith(('import ', 'from ')):
                    insert_index = i + 1
                elif line.strip() == "" and insert_index > 0:
                    break
            
            # Insert the import
            lines.insert(insert_index, import_statement + "\n")
            
            # Write back to file
            with open(file_path, 'w') as f:
                f.writelines(lines)
            
            self.logger.info(f"Added import '{import_statement}' to {file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to add import to {component}: {e}")
            return False
    
    def _commit_fix(self, commit_message: str) -> bool:
        """Commit the fix to the current branch"""
        try:
            subprocess.run(["git", "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", commit_message], check=True, capture_output=True)
            subprocess.run(["git", "push", "origin", "HEAD"], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to commit fix: {e}")
            return False
    
    def create_manual_intervention_instructions(self, error: Dict[str, Any]) -> str:
        """
        Create detailed manual intervention instructions for complex errors.
        """
        instructions = f"""
# Manual Intervention Required

**Error ID:** `{error['id']}`
**Component:** {error['component']}
**Error Type:** {error['error_type']}

## Problem Description
{error['error_message']}

## Context
{error['context']}

## Recommended Actions

1. **Analyze the Error**
   - Review the stack trace below
   - Identify the root cause
   - Consider the impact on system functionality

2. **Develop a Solution**
   - Test any fixes in a development environment first
   - Ensure the fix doesn't break existing functionality
   - Consider edge cases and error handling

3. **Implementation Steps**
   - Create a new branch: `git checkout -b fix/error-{error['id']}`
   - Implement your fix
   - Test thoroughly
   - Commit with a clear message
   - Create a pull request

4. **Verification**
   - Run all tests
   - Monitor system for 24 hours after deployment
   - Mark error as resolved if no issues occur

## Stack Trace
```
{error['stack_trace']}
```

## Additional Context
- First occurred: {error['first_occurred']}
- Occurrence count: {error['occurrence_count']}
- Severity: {error['severity']}

---
*Generated automatically by the error analysis system*
        """.strip()
        
        return instructions


# Add method to error logger for tracking fix attempts
def log_error_fix_attempted(error_id: str, fix_description: str, success: bool):
    """Mark that an automatic fix has been attempted for an error"""
    error = error_logger._find_existing_error(error_id)
    if error:
        error["fix_attempted"] = True
        error["fix_attempted_at"] = datetime.now(timezone.utc).isoformat()
        error["fix_description"] = fix_description
        error["fix_successful"] = success
        error_logger._save_errors()


# Monkey patch the method into the error logger
error_logger.log_error_fix_attempted = log_error_fix_attempted

# Global auto fixer instance
auto_fixer = AutoFixer()