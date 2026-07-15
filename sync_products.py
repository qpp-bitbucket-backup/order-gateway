"""Manual product sync script for testing."""
import sys
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.tasks.products import sync_products_from_qpmn, sync_skus_from_qpmn
from app.core.config import settings


def main():
    """Run manual product sync."""
    parser = argparse.ArgumentParser(
        description='Manual Product Sync from QPMN API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Sync products for a specific store
  python sync_products.py --store-id STORE123
  
  # Sync only SKUs for a specific store
  python sync_products.py --store-id STORE123 --skus-only
  
  # Short form
  python sync_products.py -s STORE123
        """
    )
    
    parser.add_argument(
        '-s', '--store-id',
        type=str,
        required=True,
        help='Store ID to sync products for (required)'
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Manual Product Sync from QPMN API")
    print("=" * 60)
    print()
    print(f"Store ID: {args.store_id}")
    print(f"Mode: {'Products and SKUs'}")
    print()
    print("Starting sync...")
    
    try:
        print("Syncing products ...")
        result = sync_products_from_qpmn(args.store_id)
        
        print()
        print("=" * 60)
        print("Sync Results:")
        print("=" * 60)
        print(f"Success: {result['success']}")
        
        if 'products_synced' in result:
            print(f"Products synced: {result['products_synced']}")
        
        if 'skus_synced' in result:
            print(f"SKUs synced: {result['skus_synced']}")
        
        if 'timestamp' in result:
            print(f"Timestamp: {result['timestamp']}")
        
        if 'message' in result:
            print(f"Message: {result['message']}")
        
        print("=" * 60)
        
        if not result['success']:
            sys.exit(1)
        
    except Exception as e:
        print()
        print("=" * 60)
        print(f"ERROR: Sync failed!")
        print(f"Error: {str(e)}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
