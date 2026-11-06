from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def asset_permission_required(permission_name):
    """
    Decorator that requires the current user to be authenticated
    and to hold the named permission.

    Raises 403 if the user is authenticated but lacks the permission.
    """

    def decorator(view_function):

        @wraps(view_function)
        @login_required
        def wrapped_view(*args, **kwargs):

            # Connect this to your Role & Permission Management module.
            if not current_user.has_permission(permission_name):
                abort(403)

            return view_function(*args, **kwargs)

        return wrapped_view

    return decorator