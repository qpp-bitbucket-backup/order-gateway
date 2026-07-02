"""Product sync API endpoints."""
from fastapi import APIRouter, HTTPException, Depends, Query, status
from typing import Optional
from app.core.auth_admin import verify_admin_key
from app.tasks.products import sync_products_from_qpmn, sync_skus_from_qpmn

router = APIRouter(
    prefix="/api/sync",
    tags=["Product Sync"],
    dependencies=[Depends(verify_admin_key)],
)


@router.post("/products")
def trigger_product_sync(store_id: Optional[str] = Query(None, description="Store ID to filter products")):
    """
    Trigger product synchronization from QPMN API.
    
    This endpoint starts an asynchronous Celery task to fetch products from QPMN API
    and sync them to the database.
    
    Args:
        store_id: Optional store ID to filter which products to sync
        
    Returns:
        Task ID for tracking the sync operation
    """
    try:
        # Start async task
        task = sync_products_from_qpmn.delay(store_id=store_id)
        
        return {
            "success": True,
            "message": "Product sync started",
            "task_id": task.id,
            "store_id": store_id,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start product sync: {str(e)}"
        )


@router.post("/skus")
def trigger_sku_sync(store_id: Optional[str] = Query(None, description="Store ID to filter SKUs")):
    """
    Trigger SKU synchronization from QPMN API.
    
    This endpoint starts an asynchronous Celery task to fetch SKUs from QPMN API
    and sync them to the database.
    
    Args:
        store_id: Optional store ID to filter which SKUs to sync
        
    Returns:
        Task ID for tracking the sync operation
    """
    try:
        # Start async task
        task = sync_skus_from_qpmn.delay(store_id=store_id)
        
        return {
            "success": True,
            "message": "SKU sync started",
            "task_id": task.id,
            "store_id": store_id,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start SKU sync: {str(e)}"
        )


@router.get("/status/{task_id}")
def get_sync_status(task_id: str):
    """
    Get the status of a sync task.
    
    Args:
        task_id: The Celery task ID
        
    Returns:
        Task status and result if completed
    """
    from app.core.celery import celery_app
    
    try:
        task = celery_app.AsyncResult(task_id)
        
        response = {
            "task_id": task_id,
            "status": task.status,
        }
        
        if task.status == "SUCCESS":
            response["result"] = task.result
        elif task.status == "FAILURE":
            response["error"] = str(task.result)
        
        return response
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get task status: {str(e)}"
        )
