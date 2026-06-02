import json
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from .db import get_db
from .models import User


def get_user_permissions(user: User):
    permissions = set()
    for role in user.roles:
        try:
            role_permissions = json.loads(role.permissions or "[]")
        except json.JSONDecodeError:
            role_permissions = []
        for perm in role_permissions:
            permissions.add(perm)
    return sorted(list(permissions))


def has_permission(user: User, permission: str) -> bool:
    perms = get_user_permissions(user)
    return "full_access" in perms or permission in perms


def require_permission(permission: str):
    def _permission_checker(current_user=Depends(), db: Session = Depends(get_db)):
             # current_user comes from auth.get_current_user via dependency injection in routes
        if not has_permission(current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required",
            )
        return current_user

    return _permission_checker