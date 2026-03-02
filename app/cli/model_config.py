"""
CLI tool for managing model configurations in Redis.

Usage:
    python -m app.cli.model_config list
    python -m app.cli.model_config show minimax
    python -m app.cli.model_config set minimax --name="MiniMax" --host=localhost --port=8007
    python -m app.cli.model_config update minimax --host=100.80.5.15 --port=9084
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

    print(f"\n{'Model Key':<20} {'Name':<20} {'Host':<25} {'Port':<8} {'Video':<8} {'Updated':<20}")
    print("=" * 105)

    for config in configs:
        model_key = config.get('model_key', 'N/A')
        name = config.get('name', 'N/A')
        host = config.get('host', 'N/A')
        port = str(config.get('port', 'N/A'))
        supports_video = 'Yes' if config.get('supports_video', False) else 'No'
        updated = config.get('updated_at', 'N/A')
        if updated != 'N/A' and 'T' in updated:
            updated = updated.split('T')[0]

        print(f"{model_key:<20} {name:<20} {host:<25} {port:<8} {supports_video:<8} {updated:<20}")

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

    if args.name:
        config['name'] = args.name
    if args.host:
        config['host'] = args.host
    if args.port is not None:
        config['port'] = int(args.port)
    if args.model_id is not None:
        config['model_id'] = args.model_id
    if args.supports_video is not None:
        config['supports_video'] = args.supports_video
    if args.top_p is not None:
        config['top_p'] = float(args.top_p)
    if args.top_k is not None:
        config['top_k'] = int(args.top_k)

    if not config:
        print("Error: No configuration fields provided")
        return

    required_fields = ['name', 'host', 'port']
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
    if args.host:
        updates['host'] = args.host
    if args.port is not None:
        updates['port'] = int(args.port)
    if args.model_id is not None:
        updates['model_id'] = args.model_id
    if args.supports_video is not None:
        updates['supports_video'] = args.supports_video
    if args.top_p is not None:
        updates['top_p'] = float(args.top_p)
    if args.top_k is not None:
        updates['top_k'] = int(args.top_k)

    if not updates:
        print("Error: No fields to update")
        return

    try:
        registry.update_config(model_key, updates)
        print(f"\n✓ Updated '{model_key}' — fields: {list(updates.keys())}")
        show_config(model_key)
    except ModelConfigNotFoundError as e:
        print(f"\nError: {e}")
        print(f"Available models: {e.available_keys}")


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

  # Update host/port at runtime (e.g. after switching to Tailscale direct)
  python -m app.cli.model_config update minimax --host=100.80.5.15 --port=9084

  # Set full config for a new model
  python -m app.cli.model_config set minimax --name="MiniMax" --host=localhost --port=8007

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
    set_parser.add_argument('--host', help='Host the application calls (IP, hostname, or localhost)')
    set_parser.add_argument('--port', type=int, help='Port the application calls')
    set_parser.add_argument('--model-id', help='Model ID for API calls')
    set_parser.add_argument('--supports-video', type=bool, help='Supports video input')
    set_parser.add_argument('--top-p', type=float, help='Sampling top_p')
    set_parser.add_argument('--top-k', type=int, help='Sampling top_k')

    # Update command
    update_parser = subparsers.add_parser('update', help='Partially update model configuration')
    update_parser.add_argument('model_key', help='Model key to update')
    update_parser.add_argument('--name', help='Display name')
    update_parser.add_argument('--host', help='Host the application calls')
    update_parser.add_argument('--port', type=int, help='Port the application calls')
    update_parser.add_argument('--model-id', help='Model ID for API calls')
    update_parser.add_argument('--supports-video', type=bool, help='Supports video input')
    update_parser.add_argument('--top-p', type=float, help='Sampling top_p')
    update_parser.add_argument('--top-k', type=int, help='Sampling top_k')

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
