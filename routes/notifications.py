"""
Notification API endpoints — used by the nav-bar bell dropdown.
"""

from flask import Blueprint, jsonify, request
import notification_service

bp = Blueprint("notifications", __name__)


@bp.route("/api/notifications/all")
def get_all():
    return jsonify(notification_service.get_all())


@bp.route("/api/notifications/mark_read", methods=["POST"])
def mark_read():
    data   = request.get_json(silent=True) or {}
    nid    = data.get("id")   # None = mark all
    notification_service.mark_read(nid)
    return jsonify({"unread": notification_service.get_unread_count()})
