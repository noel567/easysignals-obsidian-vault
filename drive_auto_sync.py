#!/usr/bin/env python3.13
"""
Drive Auto-Sync Agent
=====================
Auto-uploads files created during chat sessions to Google Drive with proper classification and team permissions.

Workflow:
1. Parse last chat session → Extract new outputs/files
2. Classify: Which Drive folder does this content belong to?
   - SOPs? → /Operations/SOPs/
   - Reports? → /Reports/
   - Docs? → /Documents/
   - Team-Files? → /Team/
3. Auto-upload to relevant folders (with team permissions!)
4. Set Permissions: All 6 team members (Editor)
5. Log: What uploaded? Where? Size? Link?

Usage:
  python drive_auto_sync.py [--check] [--upload] [--dry-run]
  
  --check     Only check for new files, don't upload
  --upload    Upload all detected files
  --dry-run   Show what would be uploaded without uploading
"""

import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

# Configuration
WORKSPACE_ROOT = Path("/data/.openclaw/workspace")
SCRIPTS_DIR = WORKSPACE_ROOT / "scripts"
SYNC_LOG = WORKSPACE_ROOT / ".drive_sync_log.json"

# Team members to share with (all 6)
TEAM_MEMBERS = [
    "noel@easysignals.ch",
    "livio@easysignals.ch",
    "mani@easysignals.ch",
    "lencjs@easysignals.ch",
    "tim@easysignals.ch",
    "support@easysignals.ch"  # +1
]

# File classification rules
CLASSIFICATION_RULES = {
    "sops": {
        "patterns": ["sop", "procedure", "workflow", "process", "checklist"],
        "folders": ["SOPs/", "Operations/SOPs/"],
        "drive_path": "Shared Team/SOPs/",
    },
    "reports": {
        "patterns": ["report", "summary", "brief", "analysis", "audit", "delivery"],
        "extensions": [".pdf", ".xlsx", ".csv"],
        "drive_path": "Shared Team/Reports/",
    },
    "docs": {
        "patterns": ["doc", "documentation", "guide", "instruction", "manual"],
        "extensions": [".md", ".txt", ".docx"],
        "drive_path": "Shared Team/Documents/",
    },
    "scripts": {
        "patterns": ["script", "automation", "sync", "integration"],
        "extensions": [".py", ".sh", ".js", ".ts"],
        "drive_path": "Operations/Scripts/",
    },
    "team": {
        "patterns": ["team", "shared", "common"],
        "drive_path": "Shared Team/",
    }
}

# File exclusion rules (DO NOT sync these)
EXCLUDE_PATTERNS = [
    "__pycache__",
    ".pyc",
    ".pyo",
    ".DS_Store",
    ".git",
    ".gitignore",
    "node_modules",
    ".env",
    "*.log",
    ".session",
    "*.session",
    "token",
    "secret",
    "debug",
    "test",
    "temp",
    "tmp",
    ".cache"
]

EXCLUDE_EXTENSIONS = [
    ".pyc",
    ".pyo",
    ".tmp",
    ".log",
    ".session",
    ".cache",
    ".DS_Store"
]


def should_exclude_file(filepath: Path) -> bool:
    """Check if file should be excluded from sync."""
    filepath_str = str(filepath)
    
    # Check extension exclusions
    if filepath.suffix in EXCLUDE_EXTENSIONS:
        return True
    
    # Check pattern exclusions
    for pattern in EXCLUDE_PATTERNS:
        if pattern.lower() in filepath_str.lower():
            return True
    
    return False


def is_complete_file(filepath: Path) -> bool:
    """Check if file exists and appears complete (not being written)."""
    if not filepath.exists():
        return False
    
    # Check if file is readable and has content
    try:
        size = filepath.stat().st_size
        # Skip empty files
        if size == 0:
            return False
        return True
    except Exception:
        return False


def classify_file(filepath: Path) -> Tuple[str, str]:
    """
    Classify file and return (category, drive_path).
    Returns ("unknown", "Shared Team/") if no match.
    """
    filename_lower = filepath.name.lower()
    
    for category, rules in CLASSIFICATION_RULES.items():
        # Check patterns
        patterns = rules.get("patterns", [])
        for pattern in patterns:
            if pattern in filename_lower:
                return (category, rules["drive_path"])
        
        # Check extensions
        extensions = rules.get("extensions", [])
        if filepath.suffix in extensions:
            return (category, rules["drive_path"])
    
    return ("unknown", "Shared Team/")


def get_file_size_str(filepath: Path) -> str:
    """Get human-readable file size."""
    size = filepath.stat().st_size
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def get_line_count(filepath: Path) -> int:
    """Get line count for text files."""
    if filepath.suffix in [".py", ".sh", ".js", ".ts", ".md", ".txt", ".json"]:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0
    return 0


def load_sync_log() -> Dict:
    """Load previous sync log."""
    if SYNC_LOG.exists():
        try:
            with open(SYNC_LOG, "r") as f:
                return json.load(f)
        except Exception:
            return {"synced": {}, "history": []}
    return {"synced": {}, "history": []}


def save_sync_log(log: Dict):
    """Save sync log."""
    with open(SYNC_LOG, "w") as f:
        json.dump(log, f, indent=2)


def get_new_files() -> List[Tuple[Path, str, str]]:
    """
    Get new files created since last sync.
    Returns list of (filepath, category, drive_path) tuples.
    """
    sync_log = load_sync_log()
    synced_files = sync_log.get("synced", {})
    
    new_files = []
    
    # Scan relevant directories
    scan_dirs = [
        SCRIPTS_DIR,
        WORKSPACE_ROOT / "SOPs",
        WORKSPACE_ROOT / "memory",
    ]
    
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        
        for filepath in scan_dir.rglob("*"):
            if not filepath.is_file():
                continue
            
            # Skip excluded files
            if should_exclude_file(filepath):
                continue
            
            # Skip if not complete
            if not is_complete_file(filepath):
                continue
            
            # Skip if already synced
            filepath_str = str(filepath)
            if filepath_str in synced_files:
                continue
            
            # Classify
            category, drive_path = classify_file(filepath)
            
            # Skip unknown files (too risky)
            if category == "unknown":
                continue
            
            new_files.append((filepath, category, drive_path))
    
    return new_files


def upload_file_to_drive(filepath: Path, drive_path: str, dry_run: bool = False) -> Tuple[bool, str]:
    """
    Upload file to Google Drive using google-drive CLI.
    Returns (success, file_id_or_error_msg)
    """
    if dry_run:
        return (True, "DRY_RUN")
    
    try:
        # Find or create target folder
        folder_cmd = [
            "google-drive",
            "find-folder",
            "--name",
            drive_path.strip("/")
        ]
        
        result = subprocess.run(
            folder_cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return (False, f"Folder not found: {drive_path}")
        
        try:
            folder_info = json.loads(result.stdout)
            folder_id = folder_info.get("id")
        except json.JSONDecodeError:
            return (False, f"Invalid folder response: {drive_path}")
        
        if not folder_id:
            return (False, f"No folder ID for: {drive_path}")
        
        # Upload file
        upload_cmd = [
            "google-drive",
            "upload",
            "--file",
            str(filepath),
            "--folder-id",
            folder_id,
            "--name",
            filepath.name
        ]
        
        result = subprocess.run(
            upload_cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            return (False, f"Upload failed: {result.stderr}")
        
        try:
            upload_info = json.loads(result.stdout)
            file_id = upload_info.get("id")
            return (True, file_id)
        except json.JSONDecodeError:
            return (False, f"Invalid upload response")
        
    except subprocess.TimeoutExpired:
        return (False, "Upload timeout")
    except Exception as e:
        return (False, str(e))


def share_with_team(file_id: str, dry_run: bool = False) -> Tuple[bool, str]:
    """
    Share file with all team members as Editor.
    Returns (success, message)
    """
    if dry_run:
        return (True, "DRY_RUN")
    
    try:
        for team_member in TEAM_MEMBERS:
            share_cmd = [
                "google-drive",
                "share",
                "--file-id",
                file_id,
                "--email",
                team_member,
                "--role",
                "editor"
            ]
            
            result = subprocess.run(
                share_cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                # Log warning but continue with other team members
                print(f"  ⚠️  Failed to share with {team_member}: {result.stderr}")
        
        return (True, f"Shared with {len(TEAM_MEMBERS)} team members")
    
    except Exception as e:
        return (False, str(e))


def sync_files(check_only: bool = False, dry_run: bool = False) -> Dict:
    """
    Main sync function.
    Returns summary dict with uploaded files and results.
    """
    new_files = get_new_files()
    
    if not new_files:
        return {
            "status": "ok",
            "message": "No new files to sync",
            "uploaded": [],
            "failed": []
        }
    
    sync_log = load_sync_log()
    uploaded = []
    failed = []
    
    print(f"\n🔄 Drive Auto-Sync — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📁 Found {len(new_files)} new file(s) to process\n")
    
    for filepath, category, drive_path in new_files:
        filename = filepath.name
        size_str = get_file_size_str(filepath)
        line_count = get_line_count(filepath)
        
        print(f"📄 {filename}")
        print(f"   Category: {category} | Size: {size_str}", end="")
        if line_count > 0:
            print(f" | Lines: {line_count}", end="")
        print()
        
        if check_only:
            print(f"   → Would upload to: {drive_path}\n")
            uploaded.append({
                "file": filename,
                "category": category,
                "path": drive_path,
                "status": "checked_only"
            })
            continue
        
        # Upload
        success, result = upload_file_to_drive(filepath, drive_path, dry_run=dry_run)
        
        if success:
            file_id = result
            
            # Share with team
            share_success, share_msg = share_with_team(file_id, dry_run=dry_run)
            
            if share_success or dry_run:
                status_icon = "✅" if not dry_run else "🟡"
                print(f"   {status_icon} Uploaded to: {drive_path}")
                if not dry_run:
                    print(f"   👥 {share_msg}")
                
                # Log in sync log
                sync_log["synced"][str(filepath)] = {
                    "file_id": file_id if not dry_run else "dry_run",
                    "category": category,
                    "drive_path": drive_path,
                    "timestamp": datetime.now().isoformat(),
                    "size": size_str,
                    "lines": line_count
                }
                
                uploaded.append({
                    "file": filename,
                    "category": category,
                    "path": drive_path,
                    "size": size_str,
                    "file_id": file_id if not dry_run else "dry_run",
                    "status": "uploaded"
                })
            else:
                print(f"   ❌ Share failed: {share_msg}\n")
                failed.append({
                    "file": filename,
                    "error": f"Share failed: {share_msg}"
                })
        else:
            print(f"   ❌ Upload failed: {result}\n")
            failed.append({
                "file": filename,
                "error": result
            })
    
    # Add history entry
    if uploaded and not check_only:
        sync_log["history"].append({
            "timestamp": datetime.now().isoformat(),
            "uploaded_count": len(uploaded),
            "failed_count": len(failed),
            "files": [u["file"] for u in uploaded]
        })
    
    # Save log
    if not dry_run:
        save_sync_log(sync_log)
    
    return {
        "status": "ok" if not failed else "partial",
        "uploaded": uploaded,
        "failed": failed,
        "total": len(new_files)
    }


def print_summary(result: Dict):
    """Print formatted summary."""
    print(f"\n{'='*60}")
    print(f"📊 SYNC SUMMARY")
    print(f"{'='*60}")
    
    print(f"✅ Uploaded: {len(result['uploaded'])}")
    for item in result['uploaded']:
        icon = "🟡" if item.get('status') == 'checked_only' else "✅"
        print(f"   {icon} {item['file']} → {item['path']}")
        if 'size' in item:
            print(f"      (size: {item['size']})")
    
    if result['failed']:
        print(f"\n❌ Failed: {len(result['failed'])}")
        for item in result['failed']:
            print(f"   ❌ {item['file']}: {item['error']}")
    
    print(f"\n📝 Log file: {SYNC_LOG}")
    print(f"{'='*60}\n")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Drive Auto-Sync Agent — Auto-upload files to Google Drive"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check for new files, don't upload"
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload all detected files"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be uploaded without uploading"
    )
    
    args = parser.parse_args()
    
    # Determine mode
    check_only = args.check
    dry_run = args.dry_run
    
    # Run sync
    result = sync_files(check_only=check_only, dry_run=dry_run)
    
    # Print summary
    print_summary(result)
    
    # Exit code
    sys.exit(0 if result['status'] == 'ok' else 1)


if __name__ == "__main__":
    main()
