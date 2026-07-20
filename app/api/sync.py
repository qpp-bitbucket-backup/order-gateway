"""Product sync API endpoints."""
from fastapi import APIRouter, HTTPException, Depends, Query, status
from typing import Optional
from app.core.auth_admin import verify_admin_key
from app.core.auth_jwt import require_editor_or_above
from app.models.user import User
from app.tasks.products import sync_products_task, sync_skus_task

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
        task = sync_products_task.delay(store_id=store_id)
        
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
        task = sync_skus_task.delay(store_id=store_id)
        
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


# ---------------------------------------------------------------------------
# Platform JWT router – requires JWT Bearer token (for frontend platform)
# ---------------------------------------------------------------------------

jwt_router = APIRouter(
    prefix="/api/platform",
    tags=["Platform"],
)


@jwt_router.post("/products")
def platform_trigger_product_sync(
    store_id: Optional[str] = Query(None, description="Store ID to filter products"),
    current_user: User = Depends(require_editor_or_above),
):
    """
    Trigger product synchronization from QPMN API (JWT).

    Requires JWT Bearer token with editor or admin role.
    """
    try:
        task = sync_products_task.delay(store_id=store_id)
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


@jwt_router.post("/skus")
def platform_trigger_sku_sync(
    store_id: Optional[str] = Query(None, description="Store ID to filter SKUs"),
    current_user: User = Depends(require_editor_or_above),
):
    """
    Trigger SKU synchronization from QPMN API (JWT).

    Requires JWT Bearer token with editor or admin role.
    """
    try:
        task = sync_skus_task.delay(store_id=store_id)
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


@jwt_router.get("/status/{task_id}")
def platform_get_sync_status(
    task_id: str,
    current_user: User = Depends(require_editor_or_above),
):
    """
    Get the status of a sync task (JWT).

    Requires JWT Bearer token with editor or admin role.
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
