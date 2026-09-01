"""Product sync API endpoints."""
from fastapi import APIRouter, HTTPException, Depends, Query, status
from typing import Optional
from app.core.auth_admin import verify_admin_key
from app.core.auth_jwt import require_editor_or_above, resolve_scoped_store_id
from app.models.user import User
from app.services.client import client_service
from app.tasks.products import sync_products_from_qpmn, sync_products_task, sync_skus_task

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
    ADMIN can sync any store (or all stores when store_id is omitted);
    EDITOR is limited to their own store.
    """
    # Outside the try block so the 403 for out-of-scope stores is not
    # swallowed into a 500 by the generic exception handler below.
    scoped_store_id = resolve_scoped_store_id(current_user, store_id)
    try:
        task = sync_products_task.delay(store_id=scoped_store_id)
        return {
            "success": True,
            "message": "Product sync started",
            "task_id": task.id,
            "store_id": scoped_store_id,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start product sync: {str(e)}"
        )


@jwt_router.post("/products/sync")
def platform_sync_store_products(
    store_id: str = Query(..., description="Store ID whose products should be synced"),
    current_user: User = Depends(require_editor_or_above),
):
    """
    Sync products for a specific store (JWT, runs synchronously).

    Requires JWT Bearer token with editor or admin role.
    ADMIN can sync any store; EDITOR is limited to their own store.
    The store must exist in the clients table; returns 404 otherwise.
    """
    # Non-admin users can only trigger a sync for their own store.
    resolve_scoped_store_id(current_user, store_id)
    if not client_service.get_store_key_by_id(store_id):
        # Covers both "store missing from clients" and "store_key not
        # configured" — the sync needs the key for QPMN Basic auth.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Store not found or store_key not configured: {store_id}",
        )
    # Called synchronously (not via Celery) so the response carries the
    # final result; expect the request to take as long as the sync itself.
    result = sync_products_from_qpmn(store_id)
    return {
        "success": result.get("success", False),
        "message": result.get("message", ""),
        "store_id": store_id,
        "result": result,
    }


@jwt_router.post("/skus")
def platform_trigger_sku_sync(
    store_id: Optional[str] = Query(None, description="Store ID to filter SKUs"),
    current_user: User = Depends(require_editor_or_above),
):
    """
    Trigger SKU synchronization from QPMN API (JWT).

    Requires JWT Bearer token with editor or admin role.
    ADMIN can sync any store (or all stores when store_id is omitted);
    EDITOR is limited to their own store.
    """
    # Outside the try block so the 403 for out-of-scope stores is not
    # swallowed into a 500 by the generic exception handler below.
    scoped_store_id = resolve_scoped_store_id(current_user, store_id)
    try:
        task = sync_skus_task.delay(store_id=scoped_store_id)
        return {
            "success": True,
            "message": "SKU sync started",
            "task_id": task.id,
            "store_id": scoped_store_id,
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
