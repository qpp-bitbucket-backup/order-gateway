from fastapi import APIRouter, HTTPException, Query, status, Depends
from sqlmodel import Session, select
from typing import List, Optional

from app.core.database import get_session
from app.models.product import Product, Sku
from app.schemas.product import (
    ProductsListResponse,
    Product as ProductSchema,
    SkusListResponse,
    Sku as SkuSchema,
)
from app.core.auth_oneflow import verify_oneflow_auth, get_client_store_id

router = APIRouter(
    prefix="/api",
    tags=["Products"],
    dependencies=[Depends(verify_oneflow_auth)],
)


@router.get("/product", response_model=ProductsListResponse)
def get_products(
    page: int = Query(1, ge=1, description="Page number"),
    pagesize: int = Query(100, ge=1, le=1000, description="Items per page"),
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),
):
    """
    Get Products - Retrieves a list of available products, including their components and attributes.

    Returns all active products for the authenticated client's store.
    If using bootstrap credentials, returns all active products.
    """
    try:
        # Calculate offset
        offset = (page - 1) * pagesize
        
        # Build query with optional store_id filter
        query = select(Product).where(Product.is_active == True)
        if store_id:
            query = query.where(Product.store_id == store_id)
        
        # Get total count of active products
        count_statement = query
        total_count = len(session.exec(count_statement).all())

        # Get paginated products
        statement = (
            query
            .offset(offset)
            .limit(pagesize)
            .order_by(Product.product_code)
        )
        products = session.exec(statement).all()

        # Calculate total pages
        total_pages = (total_count + pagesize - 1) // pagesize if total_count > 0 else 1

        # Build response
        product_schemas = [
            ProductSchema(
                id=product.product_id,
                productCode=product.product_code,
                description=product.description,
                components=product.components or []
            )
            for product in products
        ]

        return ProductsListResponse(
            success=True,
            count=total_count,
            page=page,
            pages=total_pages,
            data=product_schemas
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve products: {str(e)}"
        )


@router.get("/sku", response_model=SkusListResponse)
def get_skus(
    page: int = Query(1, ge=1, description="Page number"),
    pagesize: int = Query(100, ge=1, le=1000, description="Items per page"),
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),
):
    """
    Get SKUs - Retrieves a list of available SKUs.

    Returns all active SKUs for the authenticated client's store.
    If using bootstrap credentials, returns all active SKUs.
    """
    try:
        # Calculate offset
        offset = (page - 1) * pagesize

        # Build query with optional store_id filter
        # Join with products table to filter by store_id
        if store_id:
            from sqlmodel import col
            query = (
                select(Sku)
                .join(Product, col(Sku.product_id) == col(Product.product_id))
                .where(Sku.active == True, Product.store_id == store_id)
            )
            count_query = (
                select(Sku)
                .join(Product, col(Sku.product_id) == col(Product.product_id))
                .where(Sku.active == True, Product.store_id == store_id)
            )
        else:
            query = select(Sku).where(Sku.active == True)
            count_query = select(Sku).where(Sku.active == True)
        
        # Get total count of active SKUs
        total_count = len(session.exec(count_query).all())

        # Get paginated SKUs
        statement = (
            query
            .offset(offset)
            .limit(pagesize)
            .order_by(Sku.code)
        )
        skus = session.exec(statement).all()

        # Calculate total pages
        total_pages = (total_count + pagesize - 1) // pagesize if total_count > 0 else 1

        # Build response
        sku_schemas = [
            SkuSchema(
                id=sku.sku_id,
                code=sku.code,
                description=sku.description,
                productId=sku.product_id,
                active=sku.active,
                unitPrice=sku.unit_price,
                unitCost=sku.unit_cost
            )
            for sku in skus
        ]

        return SkusListResponse(
            success=True,
            count=total_count,
            page=page,
            pages=total_pages,
            data=sku_schemas
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve SKUs: {str(e)}"
        )
