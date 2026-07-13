"""Product synchronization tasks."""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime,timezone
from sqlmodel import Session, select
from app.core.celery import celery_app
from app.core.database import engine
from app.models.product import Product, Sku
from app.models.client import Client
from app.core.config import settings
from app.services.client import ClientService
import httpx

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="tasks.products.sync_products_from_qpmn")
def sync_products_from_qpmn(self, store_id: str = None) -> Dict[str, Any]:
    """
    Sync products from QPMN API to database.
    
    Args:
        store_id: Optional store ID to filter products
        
    Returns:
        Dictionary with sync results
    """
    logger.info(f"Starting product sync from QPMN API (store_id={store_id})")
    
    try:
        # Get store_key from clients table if store_id is provided
        store_key = None
        if store_id:
            print("---------------------------------------------")
            store_key = ClientService.get_store_key_by_id(store_id)
            if not store_key:
                logger.warning(f"No client found for store_id: {store_id}")
                return {
                    "success": False,
                    "message": f"No client found for store_id: {store_id}",
                    "products_synced": 0,
                    "skus_synced": 0,
                }
            logger.info(f"Found store_key for store_id: {store_id}")
        
        # Fetch products from QPMN API using store_key
        data = fetch_products_from_qpmn(store_id, store_key)
        
        
        if not data:
            logger.warning("No products received from QPMN API")
            return {
                "success": False,
                "message": "No products received from QPMN API",
                "products_synced": 0,
                "skus_synced": 0,
            }
        current_page = data.get("pageNumber")
        total_pages = data.get("totalPages")
        total_count = data.get("totalCount")
        qpmn_products = data.get("content")
        # Sync to database
        result = sync_products_to_db(qpmn_products, store_id)
        
        logger.info(
            f"Product sync completed: {result['products_synced']} products, "
            f"{result['skus_synced']} SKUs synced"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Product sync failed: {str(e)}", exc_info=True)
        raise self.retry(exc=e, countdown=60)


def fetch_products_from_qpmn(store_id: str, store_key: str, page: int =1) -> List[Dict[str, Any]]:
    """
    Fetch products from QPMN API.
    
    Args:
        store_id: Optional store ID to filter products
        store_key: Store key for API authentication (from clients table)
        
    Returns:
        List of product dictionaries from QPMN API
    """
    # Use store_key for authentication if provided, otherwise use global API key
    api_key = store_key
    
    api_url = f"{settings.QPMN_API_URL}/store/{store_id}/products"
    
    logger.info(f"Fetching products from QPMN API: {api_url}")
    
    try:
        headers = {
            "Authorization": f"Basic {api_key}",
            "Content-Type": "application/json",
        }
        
        params = {
            "page": page,
            "limit": 20
        }
        
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                api_url,
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            
            data = response.json()
            
            # Handle different response formats
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "data" in data:
                return data["data"]
            else:
                logger.warning(f"Unexpected QPMN API response format: {type(data)}")
                return []
                
    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching products from QPMN: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error fetching products from QPMN: {str(e)}")
        raise


def sync_products_to_db(
    qpmn_products: List[Dict[str, Any]], 
    store_id: str = None
) -> Dict[str, int]:
    """
    Sync products and SKUs from QPMN data to database.
    
    Args:
        qpmn_products: List of product dictionaries from QPMN API
        store_id: Store ID to associate with products
        
    Returns:
        Dictionary with counts of synced products and SKUs
    """
    logger.info(f"Syncing {len(qpmn_products)} products to database")
    
    products_synced = 0
    skus_synced = 0
    
    with Session(engine) as session:
        for qpmn_product in qpmn_products:
            try:
                # Extract product data
                product_id = qpmn_product.get("id")
                product_code = qpmn_product.get("name")
                
                if not product_id or not product_code:
                    logger.warning(f"Skipping product with missing id or code: {qpmn_product}")
                    continue
                
                # Check if product exists
                existing_product = session.exec(
                    select(Product).where(Product.product_id == product_id)
                ).first()
                
                if existing_product:
                    # Update existing product
                    existing_product.product_code = product_code
                    existing_product.description = qpmn_product.get("productDescription")
                    existing_product.components = qpmn_product.get("components", [])
                    existing_product.is_active = qpmn_product.get("isActive", True)
                    if store_id:
                        existing_product.store_id = store_id
                    logger.debug(f"Updated product: {product_code}")
                else:
                    # Create new product
                    new_product = Product(
                        product_id=product_id,
                        product_code=product_code,
                        description=qpmn_product.get("productDescription"),
                        components=qpmn_product.get("components", []),
                        is_active=qpmn_product.get("isActive", True),
                        store_id=store_id,
                    )
                    session.add(new_product)
                    logger.debug(f"Created new product: {product_code}")
                
                products_synced += 1
                
                # Sync SKUs if present
                skus = qpmn_product.get("skus", [])
                for qpmn_sku in skus:
                    sku_result = sync_sku_to_db(session, qpmn_sku, product_id, store_id)
                    if sku_result:
                        skus_synced += 1
                        
            except Exception as e:
                logger.error(f"Error syncing product {qpmn_product.get('productCode')}: {str(e)}")
                continue
        
        session.commit()
        logger.info(f"Committed {products_synced} products and {skus_synced} SKUs to database")
    
    return {
        "success": True,
        "products_synced": products_synced,
        "skus_synced": skus_synced,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def sync_sku_to_db(
    session: Session,
    qpmn_sku: Dict[str, Any],
    product_id: str,
    store_id: str = None
) -> bool:
    """
    Sync a single SKU to database.
    
    Args:
        session: Database session
        qpmn_sku: SKU data from QPMN API
        product_id: Associated product ID
        store_id: Store ID
        
    Returns:
        True if SKU was synced successfully
    """
    try:
        sku_id = qpmn_sku.get("_id") or qpmn_sku.get("id")
        sku_code = qpmn_sku.get("code")
        
        if not sku_id or not sku_code:
            logger.warning(f"Skipping SKU with missing id or code: {qpmn_sku}")
            return False
        
        # Check if SKU exists
        existing_sku = session.exec(
            select(Sku).where(Sku.sku_id == sku_id)
        ).first()
        
        if existing_sku:
            # Update existing SKU
            existing_sku.code = sku_code
            existing_sku.description = qpmn_sku.get("description")
            existing_sku.product_id = product_id
            existing_sku.active = qpmn_sku.get("active", True)
            existing_sku.unit_price = qpmn_sku.get("unitPrice") or qpmn_sku.get("price")
            existing_sku.unit_cost = qpmn_sku.get("unitCost") or qpmn_sku.get("cost")
            if store_id:
                existing_sku.store_id = store_id
            logger.debug(f"Updated SKU: {sku_code}")
        else:
            # Create new SKU
            new_sku = Sku(
                sku_id=sku_id,
                code=sku_code,
                description=qpmn_sku.get("description"),
                product_id=product_id,
                active=qpmn_sku.get("active", True),
                unit_price=qpmn_sku.get("unitPrice") or qpmn_sku.get("price"),
                unit_cost=qpmn_sku.get("unitCost") or qpmn_sku.get("cost"),
                store_id=store_id,
            )
            session.add(new_sku)
            logger.debug(f"Created new SKU: {sku_code}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error syncing SKU {qpmn_sku.get('code')}: {str(e)}")
        return False


@celery_app.task(bind=True, name="tasks.products.sync_skus_from_qpmn")
def sync_skus_from_qpmn(self, store_id: str = None) -> Dict[str, Any]:
    """
    Sync only SKUs from QPMN API (without products).
    
    Args:
        store_id: Optional store ID to filter SKUs
        
    Returns:
        Dictionary with sync results
    """
    logger.info(f"Starting SKU sync from QPMN API (store_id={store_id})")
    
    try:
        # Get store_key from clients table if store_id is provided
        store_key = None
        if store_id:
            store_key = ClientService.get_store_key_by_id(store_id)
            if not store_key:
                logger.warning(f"No client found for store_id: {store_id}")
                return {
                    "success": False,
                    "message": f"No client found for store_id: {store_id}",
                    "skus_synced": 0,
                }
            logger.info(f"Found store_key for store_id: {store_id}")
        
        # Use store_key for authentication if provided, otherwise use global API key
        api_key = store_key if store_key else settings.QPMN_API_KEY
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        params = {}
        if store_id:
            params["store_id"] = store_id
        
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                f"{settings.QPMN_API_URL}/skus",
                headers=headers,
                params=params,
            )
            response.raise_for_status()
            
            data = response.json()
            
            if isinstance(data, list):
                qpmn_skus = data
            elif isinstance(data, dict) and "data" in data:
                qpmn_skus = data["data"]
            else:
                return {
                    "success": False,
                    "message": "Unexpected response format",
                    "skus_synced": 0,
                }
        
        skus_synced = 0
        with Session(engine) as session:
            for qpmn_sku in qpmn_skus:
                product_id = qpmn_sku.get("productId") or qpmn_sku.get("product_id")
                if product_id:
                    if sync_sku_to_db(session, qpmn_sku, product_id, store_id):
                        skus_synced += 1
            
            session.commit()
        
        logger.info(f"SKU sync completed: {skus_synced} SKUs synced")
        
        return {
            "success": True,
            "skus_synced": skus_synced,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
    except Exception as e:
        logger.error(f"SKU sync failed: {str(e)}", exc_info=True)
        raise self.retry(exc=e, countdown=60)
