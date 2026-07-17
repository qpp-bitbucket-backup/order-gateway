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
    SkuUpdateRequest,
    SkuUpdateResponse,
)
from app.core.auth_oneflow import verify_oneflow_auth, get_client_store_id
from app.core.auth_admin import verify_admin_key
from app.core.auth_jwt import get_current_user, require_editor_or_above
from app.models.user import User

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


# ---------------------------------------------------------------------------
# JWT API router – requires JWT Bearer token (for frontend platform)
# ---------------------------------------------------------------------------

jwt_router = APIRouter(
    prefix="/api/platform",
    tags=["Platform"],
)


@jwt_router.get("/product", response_model=ProductsListResponse)
def jwt_get_products(
    page: int = Query(1, ge=1, description="Page number"),
    pagesize: int = Query(100, ge=1, le=1000, description="Items per page"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get Products (JWT) - Retrieves a list of available products.

    Requires JWT Bearer token. Admin/Editor/Viewer all have access.
    If user has a store_id, only returns products for that store.
    """
    try:
        offset = (page - 1) * pagesize
        user_store_id = current_user.store_id

        query = select(Product).where(Product.is_active == True)
        if user_store_id:
            query = query.where(Product.store_id == user_store_id)

        total_count = len(session.exec(query).all())
        total_pages = (total_count + pagesize - 1) // pagesize if total_count > 0 else 1

        products = session.exec(
            query.offset(offset).limit(pagesize).order_by(Product.product_code)
        ).all()

        product_schemas = [
            ProductSchema(
                id=product.product_id,
                productCode=product.product_code,
                description=product.description,
                components=product.components or [],
                createdAt=product.created_at,
                updatedAt=product.updated_at,
            )
            for product in products
        ]

        return ProductsListResponse(
            success=True,
            count=total_count,
            page=page,
            pages=total_pages,
            data=product_schemas,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve products: {str(e)}",
        )


@jwt_router.get("/sku", response_model=SkusListResponse)
def jwt_get_skus(
    page: int = Query(1, ge=1, description="Page number"),
    pagesize: int = Query(100, ge=1, le=1000, description="Items per page"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get SKUs (JWT) - Retrieves a list of available SKUs.

    Requires JWT Bearer token. Admin/Editor/Viewer all have access.
    If user has a store_id, only returns SKUs for that store.
    """
    try:
        offset = (page - 1) * pagesize

        # Scope by user's store_id (null store_id = all stores)
        user_store_id = current_user.store_id

        if user_store_id:
            from sqlmodel import col
            query = (
                select(Sku)
                .join(Product, col(Sku.product_id) == col(Product.product_id))
                .where(Sku.active == True, Product.store_id == user_store_id)
            )
            count_query = (
                select(Sku)
                .join(Product, col(Sku.product_id) == col(Product.product_id))
                .where(Sku.active == True, Product.store_id == user_store_id)
            )
        else:
            query = select(Sku).where(Sku.active == True)
            count_query = select(Sku).where(Sku.active == True)

        total_count = len(session.exec(count_query).all())
        total_pages = (total_count + pagesize - 1) // pagesize if total_count > 0 else 1

        skus = session.exec(
            query.offset(offset).limit(pagesize).order_by(Sku.code)
        ).all()

        sku_schemas = [
            SkuSchema(
                id=sku.sku_id,
                code=sku.code,
                description=sku.description,
                productId=sku.product_id,
                active=sku.active,
                unitPrice=sku.unit_price,
                unitCost=sku.unit_cost,
                createdAt=sku.created_at,
                updatedAt=sku.updated_at,
            )
            for sku in skus
        ]

        return SkusListResponse(
            success=True,
            count=total_count,
            page=page,
            pages=total_pages,
            data=sku_schemas,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve SKUs: {str(e)}",
        )


@jwt_router.get("/sku/{sku_id}", response_model=SkuUpdateResponse)
def jwt_get_sku_by_id(
    sku_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get SKU by ID (JWT) - Retrieves a single SKU by its internal sku_id.

    Requires JWT Bearer token. Admin/Editor/Viewer all have access.
    """
    try:
        sku = session.exec(select(Sku).where(Sku.sku_id == sku_id)).first()

        if not sku:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"SKU with sku_id '{sku_id}' not found",
            )

        sku_schema = SkuSchema(
            id=sku.sku_id,
            code=sku.code,
            description=sku.description,
            productId=sku.product_id,
            active=sku.active,
            unitPrice=sku.unit_price,
            unitCost=sku.unit_cost,
            properties=sku.properties,
            customizeProject=sku.customize_project,
            createdAt=sku.created_at,
            updatedAt=sku.updated_at,
        )

        return SkuUpdateResponse(
            success=True,
            message="SKU retrieved successfully",
            sku=sku_schema,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve SKU: {str(e)}",
        )


@jwt_router.put("/sku/{sku_id}", response_model=SkuUpdateResponse)
def jwt_update_sku(
    sku_id: str,
    request: SkuUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_editor_or_above),
):
    """
    Update SKU (JWT) - Updates an existing SKU by its internal sku_id.

    Requires JWT Bearer token with editor or admin role.
    """
    try:
        sku = session.exec(select(Sku).where(Sku.sku_id == sku_id)).first()

        if not sku:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"SKU with sku_id '{sku_id}' not found",
            )

        changes = []

        if request.code is not None:
            sku.code = request.code
            changes.append("code")
        if request.description is not None:
            sku.description = request.description
            changes.append("description")
        if request.productId is not None:
            sku.product_id = request.productId
            changes.append("productId")
        if request.active is not None:
            sku.active = request.active
            changes.append("active")
        if request.unitPrice is not None:
            sku.unit_price = request.unitPrice
            changes.append("unitPrice")
        if request.unitCost is not None:
            sku.unit_cost = request.unitCost
            changes.append("unitCost")
        if request.properties is not None:
            sku.properties = request.properties
            changes.append("properties")
        if request.customizeProject is not None:
            sku.customize_project = request.customizeProject
            changes.append("customizeProject")

        if not changes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one field must be provided for update.",
            )

        session.add(sku)
        session.commit()
        session.refresh(sku)

        updated_sku = SkuSchema(
            id=sku.sku_id,
            code=sku.code,
            description=sku.description,
            productId=sku.product_id,
            active=sku.active,
            unitPrice=sku.unit_price,
            unitCost=sku.unit_cost,
            properties=sku.properties,
            customizeProject=sku.customize_project,
            createdAt=sku.created_at,
            updatedAt=sku.updated_at,
        )

        return SkuUpdateResponse(
            success=True,
            message=f"SKU updated successfully (fields: {', '.join(changes)}).",
            sku=updated_sku,
        )

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SKU update failed: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Admin API router – requires x-admin-key
# ---------------------------------------------------------------------------

admin_router = APIRouter(
    prefix="/api",
    tags=["Products Admin"],
    dependencies=[Depends(verify_admin_key)],
)


@admin_router.put("/sku/{sku_id}", response_model=SkuUpdateResponse)
def update_sku(
    sku_id: str,
    request: SkuUpdateRequest,
    session: Session = Depends(get_session),
):
    """
    Update SKU - Updates an existing SKU by its internal sku_id.

    Only the fields provided in the request body will be updated.
    Requires x-admin-key header for authentication.
    """
    try:
        sku = session.exec(
            select(Sku).where(Sku.sku_id == sku_id)
        ).first()

        if not sku:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"SKU with sku_id '{sku_id}' not found",
            )

        changes = []

        if request.code is not None:
            sku.code = request.code
            changes.append("code")

        if request.description is not None:
            sku.description = request.description
            changes.append("description")

        if request.productId is not None:
            sku.product_id = request.productId
            changes.append("productId")

        if request.active is not None:
            sku.active = request.active
            changes.append("active")

        if request.unitPrice is not None:
            sku.unit_price = request.unitPrice
            changes.append("unitPrice")

        if request.unitCost is not None:
            sku.unit_cost = request.unitCost
            changes.append("unitCost")

        if request.properties is not None:
            sku.properties = request.properties
            changes.append("properties")

        if request.customizeProject is not None:
            sku.customize_project = request.customizeProject
            changes.append("customizeProject")

        if not changes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one field must be provided for update.",
            )

        session.add(sku)
        session.commit()
        session.refresh(sku)

        updated_sku = SkuSchema(
            id=sku.sku_id,
            code=sku.code,
            description=sku.description,
            productId=sku.product_id,
            active=sku.active,
            unitPrice=sku.unit_price,
            unitCost=sku.unit_cost,
            properties=sku.properties,
            customizeProject=sku.customize_project,
            createdAt=sku.created_at,
            updatedAt=sku.updated_at,
        )

        return SkuUpdateResponse(
            success=True,
            message=f"SKU updated successfully (fields: {', '.join(changes)}).",
            sku=updated_sku,
        )

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"SKU update failed: {str(e)}",
        )
