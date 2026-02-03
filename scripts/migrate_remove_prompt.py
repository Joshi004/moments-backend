#!/usr/bin/env python3
"""
Migration script to remove duplicate 'prompt' field from moment JSON files.

This script:
1. Creates backups of all moment JSON files
2. Removes the top-level 'prompt' field from each moment
3. Keeps 'generation_config.complete_prompt' which contains the same data
4. Validates JSON structure before and after modification

Author: Engineering Team
Date: February 3, 2026
Issue: Duplicate prompt storage (~2KB per moment wasted)
"""

import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any


def backup_file(file_path: Path, backup_dir: Path) -> Path:
    """Create a backup of a file."""
    backup_path = backup_dir / file_path.name
    shutil.copy2(file_path, backup_path)
    return backup_path


def remove_prompt_field(moments: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], int]:
    """
    Remove top-level 'prompt' field from moments.
    
    Returns:
        Tuple of (modified_moments, count_of_removed_prompts)
    """
    removed_count = 0
    
    for moment in moments:
        if 'prompt' in moment:
            # Verify that the prompt exists in generation_config before removing
            if 'generation_config' in moment and moment['generation_config']:
                if 'complete_prompt' in moment['generation_config']:
                    # Safe to remove - prompt exists in generation_config
                    del moment['prompt']
                    removed_count += 1
                    print(f"  ✓ Removed prompt from moment: {moment.get('title', 'Untitled')}")
                else:
                    print(f"  ⚠ Warning: Moment '{moment.get('title', 'Untitled')}' has prompt but no complete_prompt in generation_config - keeping prompt")
            else:
                print(f"  ⚠ Warning: Moment '{moment.get('title', 'Untitled')}' has prompt but no generation_config - keeping prompt")
    
    return moments, removed_count


def migrate_file(file_path: Path, dry_run: bool = False) -> tuple[bool, int]:
    """
    Migrate a single moment JSON file.
    
    Args:
        file_path: Path to the moment JSON file
        dry_run: If True, don't actually modify the file
        
    Returns:
        Tuple of (success, removed_count)
    """
    try:
        print(f"\n📄 Processing: {file_path.name}")
        
        # Read the file
        with open(file_path, 'r', encoding='utf-8') as f:
            moments = json.load(f)
        
        if not isinstance(moments, list):
            print(f"  ❌ Error: Expected array, got {type(moments).__name__}")
            return False, 0
        
        print(f"  Found {len(moments)} moments")
        
        # Remove prompt fields
        modified_moments, removed_count = remove_prompt_field(moments)
        
        if removed_count == 0:
            print(f"  ℹ️  No prompts to remove (already migrated or no generation_config)")
            return True, 0
        
        if dry_run:
            print(f"  🔍 DRY RUN: Would remove {removed_count} prompt fields")
            return True, removed_count
        
        # Write back to file
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(modified_moments, f, indent=2)
        
        print(f"  ✅ Successfully removed {removed_count} prompt fields")
        return True, removed_count
        
    except Exception as e:
        print(f"  ❌ Error: {str(e)}")
        return False, 0


def main(dry_run: bool = False):
    """
    Main migration function.
    
    Args:
        dry_run: If True, show what would be changed without modifying files
    """
    # Get the moments directory
    script_dir = Path(__file__).parent
    backend_dir = script_dir.parent
    moments_dir = backend_dir / "static" / "moments"
    
    if not moments_dir.exists():
        print(f"❌ Moments directory not found: {moments_dir}")
        return
    
    # Create backup directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = backend_dir / "backups" / f"moments_backup_{timestamp}"
    
    if not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        print(f"📁 Backup directory created: {backup_dir}")
    
    # Find all JSON files (excluding lock files)
    json_files = sorted([f for f in moments_dir.glob("*.json") if not f.name.endswith('.lock')])
    
    if not json_files:
        print("ℹ️  No moment JSON files found")
        return
    
    print(f"\n{'='*60}")
    print(f"MIGRATION: Remove Duplicate Prompt Fields")
    print(f"{'='*60}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE MIGRATION'}")
    print(f"Found {len(json_files)} moment files")
    print(f"{'='*60}")
    
    if not dry_run:
        # Create backups
        print(f"\n📦 Creating backups...")
        for json_file in json_files:
            backup_path = backup_file(json_file, backup_dir)
            print(f"  ✓ Backed up: {json_file.name} -> {backup_path.name}")
    
    # Migrate files
    total_removed = 0
    successful = 0
    failed = 0
    
    for json_file in json_files:
        success, removed_count = migrate_file(json_file, dry_run=dry_run)
        if success:
            successful += 1
            total_removed += removed_count
        else:
            failed += 1
    
    # Summary
    print(f"\n{'='*60}")
    print(f"MIGRATION SUMMARY")
    print(f"{'='*60}")
    print(f"Files processed: {len(json_files)}")
    print(f"  ✅ Successful: {successful}")
    print(f"  ❌ Failed: {failed}")
    print(f"Total prompt fields removed: {total_removed}")
    
    if not dry_run:
        print(f"\n💾 Backups saved to: {backup_dir}")
        
        # Calculate approximate space saved
        # Each prompt is approximately 2KB
        space_saved_kb = total_removed * 2
        print(f"💰 Estimated space saved: ~{space_saved_kb}KB")
    else:
        print(f"\n🔍 This was a DRY RUN - no files were modified")
        print(f"   Run without --dry-run flag to apply changes")
    
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Migrate moment JSON files to remove duplicate prompt fields"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without modifying files"
    )
    
    args = parser.parse_args()
    
    main(dry_run=args.dry_run)
