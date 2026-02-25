"""
CLI tool for managing model configurations in Redis.

Usage:
    python -m app.cli.model_config list
    python -m app.cli.model_config show minimax
    python -m app.cli.model_config set minimax --ssh-host="naresh@85.234.64.146" --ssh-remote-host=worker-17
    python -m app.cli.model_config update minimax --ssh-remote-host=worker-17
    python -m app.cli.model_config workers minimax=worker-17 qwen3_vl_fp8=worker-16
    python -m app.cli.model_config seed
    python -m app.cli.model_config seed --force
    python -m app.cli.model_config delete minimax
"""
import sys
import argparse

# Ensure app is in path
sys.path.insert(0, '/Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend')

from app.services.config_registry import get_config_registry, ModelConfigNotFoundError
from app.utils.model_config import seed_default_configs, DEFAULT_MODELS


def list_configs():
    """List all model configurations."""
    registry = get_config_registry()
    configs = registry.list_configs()
    
    if not configs:
        print("No model configs found in Redis.")
        print("Run 'python -m app.cli.model_config seed' to initialize.")
        return
    
    print(f"\n{'Model Key':<20} {'Worker':<15} {'SSH Host':<25} {'Ports':<15} {'Video':<8} {'Mode':<10} {'Updated':<20}")
    print("=" * 120)
    
    for config in configs:
        model_key = config.get('model_key', 'N/A')
        worker = config.get('ssh_remote_host', 'N/A')
        ssh_host = config.get('ssh_host', 'N/A')
        local_port = config.get('ssh_local_port', 'N/A')
        remote_port = config.get('ssh_remote_port', 'N/A')
        supports_video = 'Yes' if config.get('supports_video', False) else 'No'
        mode = config.get('connection_mode', 'tunnel')
        updated = config.get('updated_at', 'N/A')
        if updated != 'N/A' and 'T' in updated:
            updated = updated.split('T')[0]
        
        ports = f"{local_port}:{remote_port}"
        
        print(f"{model_key:<20} {worker:<15} {ssh_host:<25} {ports:<15} {supports_video:<8} {mode:<10} {updated:<20}")
    
    print(f"\nTotal: {len(configs)} model(s)")


def show_config(model_key: str):
    """Show detailed configuration for a specific model."""
    registry = get_config_registry()
    
    try:
        config = registry.get_config(model_key)
        
        print(f"\nConfiguration for '{model_key}':")
        print("=" * 60)
        for key, value in sorted(config.items()):
            print(f"  {key:<20}: {value}")
        
    except ModelConfigNotFoundError as e:
        print(f"\nError: {e}")
        print(f"Available models: {e.available_keys}")


def set_config(model_key: str, args):
    """Set/create full configuration for a model."""
    registry = get_config_registry()
    
    config = {}
    
    # Required fields
    if args.name:
        config['name'] = args.name
    if args.ssh_host:
        config['ssh_host'] = args.ssh_host
    if args.ssh_remote_host:
        config['ssh_remote_host'] = args.ssh_remote_host
    if args.ssh_local_port:
        config['ssh_local_port'] = int(args.ssh_local_port)
    if args.ssh_remote_port:
        config['ssh_remote_port'] = int(args.ssh_remote_port)
    
    # Optional fields
    if args.model_id is not None:
        config['model_id'] = args.model_id
    if args.supports_video is not None:
        config['supports_video'] = args.supports_video
    if args.top_p is not None:
        config['top_p'] = float(args.top_p)
    if args.top_k is not None:
        config['top_k'] = int(args.top_k)
    if args.connection_mode:
        config['connection_mode'] = args.connection_mode
    if args.direct_host:
        config['direct_host'] = args.direct_host
    if args.direct_port:
        config['direct_port'] = int(args.direct_port)
    
    if not config:
        print("Error: No configuration fields provided")
        return
    
    # Check if required fields are present
    required_fields = ['name', 'ssh_host', 'ssh_remote_host', 'ssh_local_port', 'ssh_remote_port']
    missing_fields = [f for f in required_fields if f not in config]
    
    if missing_fields:
        print(f"Error: Missing required fields: {missing_fields}")
        print("For partial updates, use 'update' command instead")
        return
    
    registry.set_config(model_key, config)
    print(f"\n✓ Configuration for '{model_key}' saved successfully")
    show_config(model_key)


def update_config(model_key: str, args):
    """Partially update configuration for a model."""
    registry = get_config_registry()
    
    updates = {}
    
    if args.name:
        updates['name'] = args.name
    if args.ssh_host:
        updates['ssh_host'] = args.ssh_host
    if args.ssh_remote_host:
        updates['ssh_remote_host'] = args.ssh_remote_host
    if args.ssh_local_port:
        updates['ssh_local_port'] = int(args.ssh_local_port)
    if args.ssh_remote_port:
        updates['ssh_remote_port'] = int(args.ssh_remote_port)
    if args.model_id is not None:
        updates['model_id'] = args.model_id
    if args.supports_video is not None:
        updates['supports_video'] = args.supports_video
    if args.top_p is not None:
        updates['top_p'] = float(args.top_p)
    if args.top_k is not None:
        updates['top_k'] = int(args.top_k)
    if args.connection_mode:
        updates['connection_mode'] = args.connection_mode
    if args.direct_host:
        updates['direct_host'] = args.direct_host
    if args.direct_port:
        updates['direct_port'] = int(args.direct_port)
    
    if not updates:
        print("Error: No fields to update")
        return
    
    try:
        registry.update_config(model_key, updates)
        print(f"\n✓ Updated '{model_key}' - Fields: {list(updates.keys())}")
        show_config(model_key)
    except ModelConfigNotFoundError as e:
        print(f"\nError: {e}")
        print(f"Available models: {e.available_keys}")


def update_workers(worker_mappings: list):
    """Batch update workers (quick mode)."""
    registry = get_config_registry()
    
    print("\nUpdating workers...")
    print("=" * 60)
    
    success_count = 0
    error_count = 0
    
    for mapping in worker_mappings:
        if '=' not in mapping:
            print(f"  ✗ Invalid format: '{mapping}' (expected: model_key=worker_host)")
            error_count += 1
            continue
        
        model_key, worker_host = mapping.split('=', 1)
        model_key = model_key.strip()
        worker_host = worker_host.strip()
        
        try:
            registry.update_config(model_key, {'ssh_remote_host': worker_host})
            print(f"  ✓ {model_key:<20} -> {worker_host}")
            success_count += 1
        except ModelConfigNotFoundError:
            print(f"  ✗ {model_key:<20} (not found)")
            error_count += 1
        except Exception as e:
            print(f"  ✗ {model_key:<20} (error: {e})")
            error_count += 1
    
    print("=" * 60)
    print(f"Success: {success_count}, Errors: {error_count}")


def seed_configs(force: bool = False):
    """Seed Redis with default configurations."""
    count = seed_default_configs(force=force)
    
    if force:
        print(f"\n✓ Force-seeded {count} model configs (overwrote existing)")
    else:
        print(f"\n✓ Seeded {count} model configs")
    
    print("\nDefault models seeded:")
    for model_key in DEFAULT_MODELS.keys():
        print(f"  - {model_key}")


def delete_config(model_key: str):
    """Delete a model configuration."""
    registry = get_config_registry()
    
    # Confirm deletion
    response = input(f"\nAre you sure you want to delete config for '{model_key}'? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("Deletion cancelled")
        return
    
    deleted = registry.delete_config(model_key)
    
    if deleted:
        print(f"\n✓ Configuration for '{model_key}' deleted successfully")
    else:
        print(f"\n✗ Configuration for '{model_key}' not found")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Manage model configurations in Redis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all configurations
  python -m app.cli.model_config list
  
  # Show specific config
  python -m app.cli.model_config show minimax
  
  # Quick worker update (most common)
  python -m app.cli.model_config workers minimax=worker-17 qwen3_vl_fp8=worker-16
  
  # Update single field
  python -m app.cli.model_config update minimax --ssh-remote-host=worker-17
  
  # Set full config
  python -m app.cli.model_config set minimax --name="MiniMax" --ssh-host="naresh@85.234.64.146" --ssh-remote-host=worker-17 --ssh-local-port=8007 --ssh-remote-port=7104
  
  # Seed defaults
  python -m app.cli.model_config seed
  python -m app.cli.model_config seed --force
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # List command
    subparsers.add_parser('list', help='List all model configurations')
    
    # Show command
    show_parser = subparsers.add_parser('show', help='Show specific model configuration')
    show_parser.add_argument('model_key', help='Model key to show')
    
    # Set command
    set_parser = subparsers.add_parser('set', help='Set/create full model configuration')
    set_parser.add_argument('model_key', help='Model key to set')
    set_parser.add_argument('--name', help='Display name')
    set_parser.add_argument('--ssh-host', help='SSH jump host (user@host)')
    set_parser.add_argument('--ssh-remote-host', help='SSH remote host (worker-N)')
    set_parser.add_argument('--ssh-local-port', help='Local tunnel port')
    set_parser.add_argument('--ssh-remote-port', help='Remote service port')
    set_parser.add_argument('--model-id', help='Model ID for API calls')
    set_parser.add_argument('--supports-video', type=bool, help='Supports video input')
    set_parser.add_argument('--top-p', type=float, help='Sampling top_p')
    set_parser.add_argument('--top-k', type=int, help='Sampling top_k')
    set_parser.add_argument('--connection-mode', choices=['tunnel', 'direct'],
                            help='Connection mode: tunnel or direct')
    set_parser.add_argument('--direct-host', help='Direct server hostname or IP')
    set_parser.add_argument('--direct-port', type=int, help='Direct server port')
    
    # Update command
    update_parser = subparsers.add_parser('update', help='Partially update model configuration')
    update_parser.add_argument('model_key', help='Model key to update')
    update_parser.add_argument('--name', help='Display name')
    update_parser.add_argument('--ssh-host', help='SSH jump host (user@host)')
    update_parser.add_argument('--ssh-remote-host', help='SSH remote host (worker-N)')
    update_parser.add_argument('--ssh-local-port', help='Local tunnel port')
    update_parser.add_argument('--ssh-remote-port', help='Remote service port')
    update_parser.add_argument('--model-id', help='Model ID for API calls')
    update_parser.add_argument('--supports-video', type=bool, help='Supports video input')
    update_parser.add_argument('--top-p', type=float, help='Sampling top_p')
    update_parser.add_argument('--top-k', type=int, help='Sampling top_k')
    update_parser.add_argument('--connection-mode', choices=['tunnel', 'direct'],
                               help='Connection mode: tunnel or direct')
    update_parser.add_argument('--direct-host', help='Direct server hostname or IP')
    update_parser.add_argument('--direct-port', type=int, help='Direct server port')
    
    # Workers command (quick batch update)
    workers_parser = subparsers.add_parser('workers', help='Batch update workers (quick mode)')
    workers_parser.add_argument('mappings', nargs='+', help='Worker mappings (model_key=worker_host)')
    
    # Seed command
    seed_parser = subparsers.add_parser('seed', help='Seed Redis with default configurations')
    seed_parser.add_argument('--force', action='store_true', help='Overwrite existing configs')
    
    # Delete command
    delete_parser = subparsers.add_parser('delete', help='Delete a model configuration')
    delete_parser.add_argument('model_key', help='Model key to delete')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        if args.command == 'list':
            list_configs()
        elif args.command == 'show':
            show_config(args.model_key)
        elif args.command == 'set':
            set_config(args.model_key, args)
        elif args.command == 'update':
            update_config(args.model_key, args)
        elif args.command == 'workers':
            update_workers(args.mappings)
        elif args.command == 'seed':
            seed_configs(force=args.force)
        elif args.command == 'delete':
            delete_config(args.model_key)
        else:
            parser.print_help()
    
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
