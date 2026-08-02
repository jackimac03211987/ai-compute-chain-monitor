# -*- coding: utf-8 -*-
"""Authorization helpers for immutable identity contexts."""

from admin_common import ApiError
from identity_store import AuthContext


def require_permission(auth, permission):
    if not isinstance(auth, AuthContext) or not auth.allows(permission):
        raise ApiError(403, "permission_denied", f"Permission required: {permission}")


__all__ = ["AuthContext", "require_permission"]
