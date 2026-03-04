"""
Version tracking for Crypto Sentiment Bot.
This file is automatically updated by GitHub Actions when changes are merged to main.
"""
import os
import json
from datetime import datetime
from pathlib import Path

# Current version
VERSION = "1.0.0"
BUILD_DATE = "2024-03-04T22:50:00Z"

# Path to version metadata file
VERSION_FILE = Path(__file__).parent / "version.json"

def get_version_info():
    """Get comprehensive version information."""
    info = {
        "version": VERSION,
        "build_date": BUILD_DATE,
        "last_updated": None,
        "commit_hash": None,
        "branch": None
    }
    
    # Try to load additional info from version.json if it exists
    if VERSION_FILE.exists():
        try:
            with open(VERSION_FILE) as f:
                file_data = json.load(f)
                info.update(file_data)
        except Exception:
            pass  # Use defaults if file is corrupted
    
    # Try to get git info if available
    try:
        import subprocess
        
        # Get current commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], 
            capture_output=True, 
            text=True, 
            cwd=Path(__file__).parent
        )
        if result.returncode == 0:
            info["commit_hash"] = result.stdout.strip()[:8]
    except Exception:
        pass
    
    # If no last_updated, use build_date
    if not info["last_updated"]:
        info["last_updated"] = BUILD_DATE
    
    return info

def update_version_file(version=None, commit_hash=None, branch=None):
    """Update the version metadata file (used by CI/CD)."""
    info = {
        "version": version or VERSION,
        "build_date": BUILD_DATE,
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "commit_hash": commit_hash,
        "branch": branch
    }
    
    try:
        with open(VERSION_FILE, 'w') as f:
            json.dump(info, f, indent=2)
        return True
    except Exception as e:
        print(f"Failed to update version file: {e}")
        return False

def get_version_string():
    """Get a formatted version string."""
    info = get_version_info()
    version_str = f"v{info['version']}"
    
    if info.get('commit_hash'):
        version_str += f" ({info['commit_hash']})"
    
    return version_str

def get_last_update_string():
    """Get a formatted last update string."""
    info = get_version_info()
    try:
        last_updated = datetime.fromisoformat(info['last_updated'].replace('Z', '+00:00'))
        return last_updated.strftime('%Y-%m-%d %H:%M UTC')
    except Exception:
        return "Unknown"

if __name__ == "__main__":
    # Print version info for debugging
    import json
    print(json.dumps(get_version_info(), indent=2))