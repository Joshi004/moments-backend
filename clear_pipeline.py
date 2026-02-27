#!/usr/bin/env python3
"""
Emergency script to clear all running pipelines, kill workers, and reset state.
Use this when pipelines are stuck or you need a completely fresh start.
"""
import sys
import os
import asyncio
import time
import signal
import logging
import argparse
from pathlib import Path
from typing import List, Set

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.redis import get_async_redis_client
from app.services.pipeline.lock import release_lock

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


class PipelineCleanup:
    """Comprehensive pipeline cleanup manager."""
    
    STREAM_KEY = "pipeline:requests"
    GROUP_NAME = "pipeline_workers"
    
    def __init__(self, kill_api: bool = False, force_kill: bool = False, reset_group: bool = False):
        """
        Initialize cleanup manager.
        
        Args:
            kill_api: Whether to kill API server too
            force_kill: Use SIGKILL immediately instead of SIGTERM
            reset_group: Destroy and recreate consumer group
        """
        self.kill_api = kill_api
        self.force_kill = force_kill
        self.reset_group = reset_group
        self.redis = None
        self.backend_dir = Path(__file__).parent
        self.stats = {
            'workers_killed': 0,
            'api_killed': 0,
            'locks_released': 0,
            'cancel_flags_cleared': 0,
            'active_statuses_cleared': 0,
            'stream_messages_deleted': 0,
            'pending_messages_acked': 0,
            'pid_files_removed': 0,
        }

    async def connect(self) -> None:
        """Establish async Redis connection."""
        self.redis = await get_async_redis_client()
    
    def _find_processes_by_pattern(self, pattern: str) -> List[int]:
        """Find process PIDs by pattern using pgrep."""
        try:
            import subprocess
            result = subprocess.run(
                ['pgrep', '-f', pattern],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                pids = [int(pid) for pid in result.stdout.strip().split('\n') if pid]
                return pids
            return []
        except Exception as e:
            logger.debug(f"Error finding processes by pattern '{pattern}': {e}")
            return []
    
    def _read_pid_file(self, pid_file: Path) -> int:
        """Read PID from file."""
        try:
            if pid_file.exists():
                pid = int(pid_file.read_text().strip())
                # Verify process exists
                try:
                    os.kill(pid, 0)  # Signal 0 just checks if process exists
                    return pid
                except OSError:
                    logger.debug(f"Stale PID file: {pid_file}")
                    return 0
        except Exception as e:
            logger.debug(f"Error reading PID file {pid_file}: {e}")
        return 0
    
    def _kill_process(self, pid: int, name: str = "process") -> bool:
        """
        Kill a process by PID.
        
        Args:
            pid: Process ID
            name: Process name for logging
            
        Returns:
            True if killed successfully
        """
        try:
            # Check if process exists
            os.kill(pid, 0)
        except OSError:
            logger.debug(f"Process {pid} ({name}) doesn't exist")
            return False
        
        try:
            if self.force_kill:
                logger.info(f"  - Force killing PID {pid} ({name}) with SIGKILL")
                os.kill(pid, signal.SIGKILL)
            else:
                logger.info(f"  - Terminating PID {pid} ({name}) with SIGTERM")
                os.kill(pid, signal.SIGTERM)
                
                # Wait up to 3 seconds for graceful shutdown
                for _ in range(6):
                    time.sleep(0.5)
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        # Process is gone
                        logger.info(f"    ✓ Process {pid} terminated gracefully")
                        return True
                
                # Still running, force kill
                logger.info(f"    ! Process {pid} didn't terminate, using SIGKILL")
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.5)
            
            logger.info(f"    ✓ Process {pid} killed")
            return True
            
        except Exception as e:
            logger.warning(f"    ! Error killing process {pid}: {e}")
            return False
    
    def kill_workers(self) -> None:
        """Kill all pipeline worker processes."""
        logger.info("\n1. Killing worker processes...")
        
        worker_pids: Set[int] = set()
        
        # Find workers by PID files
        pid_files = [
            self.backend_dir / '.pids' / 'worker.pid',
            self.backend_dir / 'worker.pid',
        ]
        
        for pid_file in pid_files:
            pid = self._read_pid_file(pid_file)
            if pid > 0:
                logger.info(f"   - Found PID {pid} from {pid_file}")
                worker_pids.add(pid)
        
        # Find workers by process pattern
        pattern_pids = self._find_processes_by_pattern(r'python.*run_worker\.py')
        if pattern_pids:
            logger.info(f"   - Found {len(pattern_pids)} worker(s) via pgrep: {pattern_pids}")
            worker_pids.update(pattern_pids)
        
        if not worker_pids:
            logger.info("   ✓ No worker processes found")
            return
        
        logger.info(f"\n   Killing {len(worker_pids)} worker process(es)...")
        for pid in worker_pids:
            if self._kill_process(pid, "worker"):
                self.stats['workers_killed'] += 1
        
        logger.info(f"   ✓ Killed {self.stats['workers_killed']} worker(s)")
    
    def kill_api(self) -> None:
        """Kill API server process."""
        logger.info("\n2. Killing API server...")
        
        api_pid_file = self.backend_dir / '.pids' / 'api.pid'
        api_pid = self._read_pid_file(api_pid_file)
        
        if api_pid > 0:
            logger.info(f"   - Found API server PID {api_pid}")
            if self._kill_process(api_pid, "API server"):
                self.stats['api_killed'] += 1
                logger.info("   ✓ API server killed")
        else:
            logger.info("   ✓ No API server found")
    
    async def clear_redis_state(self) -> None:
        """Clear all Redis pipeline state."""
        step = 2 if not self.kill_api else 3
        logger.info(f"\n{step}. Clearing Redis state...")
        
        # 1. Release all locks
        lock_pattern = "pipeline:*:lock"
        locks = await self.redis.keys(lock_pattern)
        
        if locks:
            logger.info(f"   - Releasing {len(locks)} pipeline lock(s)")
            for lock_key in locks:
                video_id = lock_key.split(":")[1]
                await release_lock(video_id)
                self.stats['locks_released'] += 1
        
        # 2. Clear cancellation flags
        cancel_pattern = "pipeline:*:cancel"
        cancel_flags = await self.redis.keys(cancel_pattern)
        
        if cancel_flags:
            logger.info(f"   - Clearing {len(cancel_flags)} cancellation flag(s)")
            for flag in cancel_flags:
                await self.redis.delete(flag)
                self.stats['cancel_flags_cleared'] += 1
        
        # 3. Clear active statuses
        active_pattern = "pipeline:*:active"
        active_statuses = await self.redis.keys(active_pattern)
        
        if active_statuses:
            logger.info(f"   - Clearing {len(active_statuses)} active status(es)")
            for status_key in active_statuses:
                await self.redis.delete(status_key)
                self.stats['active_statuses_cleared'] += 1
        
        # 4. Delete all stream messages
        try:
            stream_info = await self.redis.xinfo_stream(self.STREAM_KEY)
            pending_count = stream_info.get('length', 0)
            
            if pending_count > 0:
                logger.info(f"   - Deleting {pending_count} pending request(s) from stream")
                messages = await self.redis.xrange(self.STREAM_KEY)
                
                for message_id, _ in messages:
                    await self.redis.xdel(self.STREAM_KEY, message_id)
                    self.stats['stream_messages_deleted'] += 1
        except Exception as e:
            logger.debug(f"Stream doesn't exist or error: {e}")
        
        logger.info(f"   ✓ Redis state cleared")
    
    async def reset_consumer_group(self) -> None:
        """Reset consumer group by acknowledging pending messages and optionally recreating."""
        step = 3 if not self.kill_api else 4
        logger.info(f"\n{step}. Resetting consumer group...")
        
        try:
            # Get pending messages from all consumers
            pending_info = await self.redis.xpending(self.STREAM_KEY, self.GROUP_NAME)
            
            if pending_info and pending_info[0] > 0:
                pending_count = pending_info[0]
                logger.info(f"   - Found {pending_count} pending message(s)")
                
                # Get detailed pending info
                pending_details = await self.redis.xpending_range(
                    self.STREAM_KEY,
                    self.GROUP_NAME,
                    min="-",
                    max="+",
                    count=1000
                )
                
                # Acknowledge all pending messages
                message_ids = [msg['message_id'] for msg in pending_details]
                if message_ids:
                    logger.info(f"   - Acknowledging {len(message_ids)} message(s)")
                    await self.redis.xack(self.STREAM_KEY, self.GROUP_NAME, *message_ids)
                    self.stats['pending_messages_acked'] = len(message_ids)
            
            # Optionally destroy and recreate group for complete reset
            if self.reset_group:
                logger.info(f"   - Destroying consumer group '{self.GROUP_NAME}'")
                try:
                    await self.redis.xgroup_destroy(self.STREAM_KEY, self.GROUP_NAME)
                    logger.info(f"   - Recreating consumer group '{self.GROUP_NAME}'")
                    await self.redis.xgroup_create(
                        self.STREAM_KEY,
                        self.GROUP_NAME,
                        id="0",
                        mkstream=True
                    )
                    logger.info("   ✓ Consumer group recreated")
                except Exception as e:
                    logger.warning(f"   ! Error resetting group: {e}")
            else:
                logger.info("   ✓ Pending messages acknowledged")
                
        except Exception as e:
            logger.debug(f"Consumer group doesn't exist or error: {e}")
            logger.info("   ✓ No consumer group to reset")
    
    def cleanup_pid_files(self) -> None:
        """Remove PID files."""
        step = 4 if not self.kill_api else 5
        logger.info(f"\n{step}. Cleaning up PID files...")
        
        pid_files = [
            self.backend_dir / '.pids' / 'worker.pid',
            self.backend_dir / 'worker.pid',
        ]
        
        if self.kill_api:
            pid_files.append(self.backend_dir / '.pids' / 'api.pid')
        
        for pid_file in pid_files:
            if pid_file.exists():
                try:
                    pid_file.unlink()
                    logger.info(f"   - Removed {pid_file.name}")
                    self.stats['pid_files_removed'] += 1
                except Exception as e:
                    logger.warning(f"   ! Error removing {pid_file}: {e}")
        
        if self.stats['pid_files_removed'] == 0:
            logger.info("   ✓ No PID files to remove")
        else:
            logger.info(f"   ✓ Removed {self.stats['pid_files_removed']} PID file(s)")
    
    def print_summary(self) -> None:
        """Print cleanup summary."""
        logger.info("\n" + "=" * 60)
        logger.info("CLEANUP COMPLETE - Ready for fresh start")
        logger.info("=" * 60)
        logger.info("\nSummary:")
        logger.info(f"  - Workers killed: {self.stats['workers_killed']}")
        if self.kill_api:
            logger.info(f"  - API server killed: {self.stats['api_killed']}")
        logger.info(f"  - Locks released: {self.stats['locks_released']}")
        logger.info(f"  - Cancel flags cleared: {self.stats['cancel_flags_cleared']}")
        logger.info(f"  - Active statuses cleared: {self.stats['active_statuses_cleared']}")
        logger.info(f"  - Stream messages deleted: {self.stats['stream_messages_deleted']}")
        logger.info(f"  - Pending messages acknowledged: {self.stats['pending_messages_acked']}")
        logger.info(f"  - PID files removed: {self.stats['pid_files_removed']}")
        logger.info("\n✓ You can now start fresh with ./start_backend.sh")
        logger.info("=" * 60)
    
    async def run(self) -> None:
        """Execute full cleanup."""
        logger.info("=" * 60)
        logger.info("PIPELINE CLEANUP - FULL RESET")
        logger.info("=" * 60)
        
        # Establish Redis connection before any Redis operations
        await self.connect()
        
        # Kill workers first
        self.kill_workers()
        
        # Kill API if requested
        if self.kill_api:
            self.kill_api()
        
        # Clear Redis state
        await self.clear_redis_state()
        
        # Reset consumer group
        await self.reset_consumer_group()
        
        # Clean up PID files
        self.cleanup_pid_files()
        
        # Print summary
        self.print_summary()


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description='Emergency pipeline cleanup - kills workers, clears Redis state, resets everything',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                    # Clean workers and Redis only
  %(prog)s --all              # Also kill API server
  %(prog)s --force            # Force kill with SIGKILL
  %(prog)s --reset-group      # Destroy and recreate consumer group
  %(prog)s --all --force      # Nuclear option - force kill everything
        """
    )
    
    parser.add_argument(
        '--all', '-a',
        action='store_true',
        help='Kill API server too (default: workers only)'
    )
    
    parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Force kill (SIGKILL) without waiting for graceful shutdown'
    )
    
    parser.add_argument(
        '--reset-group',
        action='store_true',
        help='Destroy and recreate Redis consumer group for complete reset'
    )
    
    args = parser.parse_args()
    
    async def _run_cleanup() -> int:
        try:
            cleanup = PipelineCleanup(
                kill_api=args.all,
                force_kill=args.force,
                reset_group=args.reset_group
            )
            await cleanup.run()
            return 0
        except KeyboardInterrupt:
            logger.info("\n\nCleanup interrupted by user")
            return 1
        except Exception as e:
            logger.error(f"\n\nError during cleanup: {e}", exc_info=True)
            return 1

    return asyncio.run(_run_cleanup())


if __name__ == "__main__":
    sys.exit(main())
