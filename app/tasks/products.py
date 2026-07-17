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
from lxml import html
import re
from app.services.client import client_service
import httpx

logger = logging.getLogger(__name__)


def sync_products_from_qpmn(store_id: str = None) -> Dict[str, Any]:
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
            store_key = client_service.get_store_key_by_id(store_id)
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
        
        # Fetch remaining pages if total_pages > current_page
        while total_pages > current_page:
            current_page += 1
            logger.info(f"Fetching page {current_page}/{total_pages}")
            data = fetch_products_from_qpmn(store_id, store_key, page=current_page)
            if data and data.get("content"):
                qpmn_products.extend(data.get("content"))
            else:
                logger.warning(f"No content returned for page {current_page}")
                break
        
        logger.info(f"Fetched total {len(qpmn_products)} products from {total_pages} page(s)")
        # Sync to database
        result = sync_products_to_db(qpmn_products, store_id)
        
        logger.info(
            f"Product sync completed: {result['products_synced']} products, "
            f"{result['skus_synced']} SKUs synced"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Product sync failed: {str(e)}", exc_info=True)
        raise


@celery_app.task(bind=True, name="tasks.products.sync_products_from_qpmn")
def sync_products_task(self, store_id: str = None) -> Dict[str, Any]:
    """Celery task wrapper for sync_products_from_qpmn."""
    return sync_products_from_qpmn(store_id)


def format_html_desc(html_text):
    html_text = re.sub(r'<style[^>]*>.*?</style>', '', html_text, flags=re.DOTALL)
    
    tree = html.fromstring(html_text)
    text = tree.text_content()
    
    text = ' '.join(text.split())
    
    return text[:255]

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
    
    api_url = f"{settings.QPMN_API_URL}/stores/{store_id}/products"
    
    logger.info(f"Fetching products from QPMN API: {api_url}")
    
    try:
        headers = {
            "Authorization": f"Basic {api_key}",
            "Content-Type": "application/json",
        }
        
        params = {
            "page": page,
            "limit": 20,
            # "sort":[{"property":"createdDate","direction":"DESC"}]
        }
        
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                api_url,
                headers=headers,
                params=params
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
    products_deleted = 0
    skus_deleted = 0
    
    # Collect all QPMN product and SKU IDs for deletion detection
    qpmn_product_ids = set()
    qpmn_sku_ids = set()
    
    with Session(engine) as session:
        for qpmn_product in qpmn_products:
            try:
                # Extract product data
                product_id = qpmn_product.get("_id") or qpmn_product.get("id")
                product_code = qpmn_product.get("name")
                
                if not product_id or not product_code:
                    logger.warning(f"Skipping product with missing id or code: {qpmn_product}")
                    continue
                
                qpmn_product_ids.add(product_id)
                
                # Collect SKU IDs from product data
                sku_id = qpmn_product.get("_id") or qpmn_product.get("id")
                if sku_id:
                    qpmn_sku_ids.add(sku_id)
                
                # Check if product exists
                existing_product = session.exec(
                    select(Product).where(Product.product_id == product_id)
                ).first()
                
                if existing_product:
                    # Update existing product
                    existing_product.product_code = product_code
                    existing_product.description = format_html_desc(qpmn_product.get("description"))
                    existing_product.components = qpmn_product.get("components", [])
                    existing_product.is_active = qpmn_product.get("isActive", True)
                    existing_product.updated_at = datetime.utcnow()
                    if store_id:
                        existing_product.store_id = store_id
                    logger.debug(f"Updated product: {product_code}")
                else:
                    # Create new product
                    new_product = Product(
                        product_id=product_id,
                        product_code=product_code,
                        description=format_html_desc(qpmn_product.get("description")),
                        components=qpmn_product.get("components", []),
                        is_active=qpmn_product.get("isActive", True),
                        store_id=store_id,
                    )
                    session.add(new_product)
                    logger.debug(f"Created new product: {product_code}")
                
                products_synced += 1

                # for qpmn_sku in skus:
                sku_result = sync_sku_to_db(session, qpmn_product, product_id, store_id)
                if sku_result:
                    skus_synced += 1
                        
            except Exception as e:
                logger.error(f"Error syncing product {qpmn_product.get('productCode')}: {str(e)}")
                continue
        
        # Delete products not in QPMN response (for this store)
        if store_id:
            db_products = session.exec(
                select(Product).where(Product.store_id == store_id)
            ).all()
        else:
            db_products = session.exec(select(Product)).all()
        
        for db_product in db_products:
            if db_product.product_id not in qpmn_product_ids:
                logger.info(f"Deleting product not in QPMN: {db_product.product_code} ({db_product.product_id})")
                # Delete associated SKUs first
                associated_skus = session.exec(
                    select(Sku).where(Sku.product_id == db_product.product_id)
                ).all()
                for sku in associated_skus:
                    session.delete(sku)
                    skus_deleted += 1
                session.delete(db_product)
                products_deleted += 1
        
        # Delete SKUs not in QPMN response (for this store)
        if store_id:
            db_skus = session.exec(
                select(Sku).where(Sku.store_id == store_id)
            ).all()
        else:
            db_skus = session.exec(select(Sku)).all()
        
        for db_sku in db_skus:
            if db_sku.sku_id not in qpmn_sku_ids:
                logger.info(f"Deleting SKU not in QPMN: {db_sku.code} ({db_sku.sku_id})")
                session.delete(db_sku)
                skus_deleted += 1
        
        session.commit()
        logger.info(
            f"Sync complete: {products_synced} products synced, {skus_synced} SKUs synced, "
            f"{products_deleted} products deleted, {skus_deleted} SKUs deleted"
        )
    
    return {
        "success": True,
        "products_synced": products_synced,
        "skus_synced": skus_synced,
        "products_deleted": products_deleted,
        "skus_deleted": skus_deleted,
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
        sku_code = qpmn_sku.get("name")
        
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
            existing_sku.description = format_html_desc(qpmn_sku.get("description"))
            existing_sku.product_id = product_id
            existing_sku.active = qpmn_sku.get("active", True)
            existing_sku.unit_price = qpmn_sku.get("unitPrice") or qpmn_sku.get("price")
            existing_sku.unit_cost = qpmn_sku.get("unitCost") or qpmn_sku.get("cost")
            # Update updated_at with modifiedDate from QPMN if it's newer
            qpmn_modified = qpmn_sku.get("modifiedDate")
            if qpmn_modified:
                # Convert timestamp (milliseconds or seconds) to datetime (timezone-naive to match DB)
                if isinstance(qpmn_modified, (int, float)):
                    # Handle both milliseconds and seconds timestamps
                    if qpmn_modified > 1e12:  # milliseconds
                        qpmn_modified_dt = datetime.fromtimestamp(qpmn_modified / 1000, tz=timezone.utc).replace(tzinfo=None)
                    else:  # seconds
                        qpmn_modified_dt = datetime.fromtimestamp(qpmn_modified, tz=timezone.utc).replace(tzinfo=None)
                    # Only update if QPMN modifiedDate is newer
                    if qpmn_modified_dt > existing_sku.updated_at:
                        existing_sku.updated_at = qpmn_modified_dt
            if store_id:
                existing_sku.store_id = store_id
            logger.debug(f"Updated SKU: {sku_code}")
        else:
            # Create new SKU
            new_sku = Sku(
                sku_id=sku_id,
                code=sku_code,
                description= format_html_desc(qpmn_sku.get("description")),
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


def sync_skus_from_qpmn(store_id: str = None) -> Dict[str, Any]:
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
            store_key = client_service.get_store_key_by_id(store_id)
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
        raise


@celery_app.task(bind=True, name="tasks.products.sync_skus_from_qpmn")
def sync_skus_task(self, store_id: str = None) -> Dict[str, Any]:
    """Celery task wrapper for sync_skus_from_qpmn."""
    return sync_skus_from_qpmn(store_id)


@celery_app.task(bind=True, name="tasks.products.sync_all_stores_products")
def sync_all_stores_products(self) -> Dict[str, Any]:
    """
    Celery Beat task to sync products for all stores.
    Iterates through all clients and syncs their products.
    """
    logger.info("[Celery Beat] Starting product sync for all stores")
    
    results = []
    with Session(engine) as session:
        clients = session.exec(select(Client)).all()
        
        if not clients:
            logger.warning("[Celery Beat] No clients/stores found in database")
            return {
                "success": False,
                "message": "No clients/stores found",
                "stores_synced": 0,
            }
        
        for client in clients:
            store_id = client.store_id
            logger.info(f"[Celery Beat] Syncing products for store: {store_id}")
            
            try:
                result = sync_products_from_qpmn(store_id)
                results.append({
                    "store_id": store_id,
                    "success": result.get("success"),
                    "products_synced": result.get("products_synced", 0),
                    "skus_synced": result.get("skus_synced", 0),
                })
                logger.info(f"[Celery Beat] Store {store_id}: {result.get('products_synced', 0)} products, {result.get('skus_synced', 0)} SKUs synced")
            except Exception as e:
                logger.error(f"[Celery Beat] Failed to sync products for store {store_id}: {e}", exc_info=True)
                results.append({
                    "store_id": store_id,
                    "success": False,
                    "error": str(e),
                })
    
    logger.info(f"[Celery Beat] Product sync completed for {len(clients)} stores")
    return {
        "success": True,
        "stores_synced": len(clients),
        "results": results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
