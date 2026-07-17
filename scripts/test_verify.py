"""Verify all user-related imports and basic functionality."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

try:
    from app.main import app
    print("[OK] App loaded OK")
except Exception as e:
    print(f"[FAIL] App load failed: {e}")

try:
    from app.models.user import User, UserRole
    print(f"[OK] User model OK (roles: {[r.value for r in UserRole]})")
except Exception as e:
    print(f"[FAIL] User model failed: {e}")

try:
    from app.core.auth_jwt import get_current_user, require_admin, require_editor_or_above
    print("[OK] Auth JWT OK")
except Exception as e:
    print(f"[FAIL] Auth JWT failed: {e}")

try:
    from app.services.user import user_service
    print("[OK] User service OK")
except Exception as e:
    print(f"[FAIL] User service failed: {e}")

try:
    from app.api.users import router
    print(f"[OK] Users API OK (routes: {len(router.routes)})")
except Exception as e:
    print(f"[FAIL] Users API failed: {e}")

try:
    from app.core.security import get_password_hash, verify_password, create_access_token
    hashed = get_password_hash("test123")
    assert verify_password("test123", hashed)
    print("[OK] Password hashing OK")
except Exception as e:
    print(f"[FAIL] Password hashing failed: {e}")

# List all registered routes via OpenAPI schema
print("\n── Registered API Routes ──")
openapi_schema = app.openapi()
for path, path_item in sorted(openapi_schema.get("paths", {}).items()):
    for method in path_item.keys():
        if method in {"get", "post", "put", "patch", "delete"}:
            print(f"  {method.upper():6s} {path}")
