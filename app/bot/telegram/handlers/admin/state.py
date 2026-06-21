from __future__ import annotations

from app.bot.telegram.fsm_utils import admin_utils_has_waiter
from app.bot.telegram.handlers.admin.orders import _get_admin_orders_state, _save_admin_orders_state
from app.bot.telegram.handlers.content_utils_admin import reset_content_utils_state
from app.bot.telegram.handlers.faq_admin import reset_faq_admin_state
from app.core.container import AppContainer

def _get_admin_broadcast_state(session) -> dict:
    block = session.state_data.get("_admin_broadcast")
    if isinstance(block, dict):
        return {
            "awaiting_payload": bool(block.get("awaiting_payload")),
            "audience": str(block.get("audience")) if block.get("audience") else None,
        }
    return {"awaiting_payload": False, "audience": None}


async def _save_admin_broadcast_state(container: AppContainer, session, state: dict) -> None:
    payload = dict(session.state_data)
    payload["_admin_broadcast"] = {
        "awaiting_payload": bool(state.get("awaiting_payload")),
        "audience": state.get("audience"),
    }
    session.state_data = payload
    await container.session_repo.save(session)


def _get_admin_utils_state(session) -> dict:
    block = session.state_data.get("_admin_utils")
    if isinstance(block, dict):
        return {
            "awaiting_payment_text": bool(block.get("awaiting_payment_text")),
            "awaiting_payment_media": bool(block.get("awaiting_payment_media")),
            "awaiting_backup_target": bool(block.get("awaiting_backup_target")),
            "awaiting_payment_review_target": bool(block.get("awaiting_payment_review_target")),
            "awaiting_prohibited_text": bool(block.get("awaiting_prohibited_text")),
            "awaiting_prohibited_media": bool(block.get("awaiting_prohibited_media")),
            "awaiting_delivery_text": bool(block.get("awaiting_delivery_text")),
            "awaiting_delivery_media": bool(block.get("awaiting_delivery_media")),
            "awaiting_contacts_text": bool(block.get("awaiting_contacts_text")),
            "awaiting_contacts_media": bool(block.get("awaiting_contacts_media")),
            "awaiting_profile_search_query": bool(block.get("awaiting_profile_search_query")),
            "profile_search_mode": str(block.get("profile_search_mode")) if block.get("profile_search_mode") else None,
            "awaiting_block_search_query": bool(block.get("awaiting_block_search_query")),
            "block_search_mode": str(block.get("block_search_mode")) if block.get("block_search_mode") else None,
            "block_operation": str(block.get("block_operation")) if block.get("block_operation") else None,
            "awaiting_codes_add": bool(block.get("awaiting_codes_add")),
            "awaiting_codes_remove": bool(block.get("awaiting_codes_remove")),
            "awaiting_faq_media_section_id": (
                int(block.get("awaiting_faq_media_section_id"))
                if block.get("awaiting_faq_media_section_id")
                else None
            ),
            "faq_admin_screen": str(block.get("faq_admin_screen")) if block.get("faq_admin_screen") else None,
            "faq_admin_nav_section_id": (
                int(block.get("faq_admin_nav_section_id"))
                if block.get("faq_admin_nav_section_id") is not None
                else None
            ),
            "faq_admin_pick_nav_section_id": (
                int(block.get("faq_admin_pick_nav_section_id"))
                if block.get("faq_admin_pick_nav_section_id") is not None
                else None
            ),
            "faq_admin_target_section_id": (
                int(block.get("faq_admin_target_section_id"))
                if block.get("faq_admin_target_section_id") is not None
                else None
            ),
            "faq_admin_panel_chat_id": (
                int(block.get("faq_admin_panel_chat_id"))
                if block.get("faq_admin_panel_chat_id")
                else None
            ),
            "faq_admin_panel_message_id": (
                int(block.get("faq_admin_panel_message_id"))
                if block.get("faq_admin_panel_message_id")
                else None
            ),
            "content_utils_kind": str(block.get("content_utils_kind")) if block.get("content_utils_kind") else None,
            "content_utils_screen": str(block.get("content_utils_screen")) if block.get("content_utils_screen") else None,
            "content_utils_panel_chat_id": (
                int(block.get("content_utils_panel_chat_id"))
                if block.get("content_utils_panel_chat_id")
                else None
            ),
            "content_utils_panel_message_id": (
                int(block.get("content_utils_panel_message_id"))
                if block.get("content_utils_panel_message_id")
                else None
            ),
            "awaiting_content_utils_media": (
                str(block.get("awaiting_content_utils_media"))
                if block.get("awaiting_content_utils_media")
                else None
            ),
            "awaiting_admin_add_id": bool(block.get("awaiting_admin_add_id")),
            "awaiting_admin_add_code": bool(block.get("awaiting_admin_add_code")),
            "awaiting_block_reason_for_code": (
                str(block.get("awaiting_block_reason_for_code")) if block.get("awaiting_block_reason_for_code") else None
            ),
            "awaiting_profile_comment_code": (
                str(block.get("awaiting_profile_comment_code")) if block.get("awaiting_profile_comment_code") else None
            ),
            "awaiting_profile_edit_code": (
                str(block.get("awaiting_profile_edit_code")) if block.get("awaiting_profile_edit_code") else None
            ),
            "awaiting_profile_edit_field": (
                str(block.get("awaiting_profile_edit_field")) if block.get("awaiting_profile_edit_field") else None
            ),
        }
    return {
        "awaiting_payment_text": False,
        "awaiting_payment_media": False,
        "awaiting_backup_target": False,
        "awaiting_payment_review_target": False,
        "awaiting_prohibited_text": False,
        "awaiting_prohibited_media": False,
        "awaiting_delivery_text": False,
        "awaiting_delivery_media": False,
        "awaiting_contacts_text": False,
        "awaiting_contacts_media": False,
        "awaiting_profile_search_query": False,
        "profile_search_mode": None,
        "awaiting_block_search_query": False,
        "block_search_mode": None,
        "block_operation": None,
        "awaiting_codes_add": False,
        "awaiting_codes_remove": False,
        "awaiting_faq_media_section_id": None,
        "faq_admin_screen": None,
        "faq_admin_nav_section_id": None,
        "faq_admin_pick_nav_section_id": None,
        "faq_admin_target_section_id": None,
        "faq_admin_panel_chat_id": None,
        "faq_admin_panel_message_id": None,
        "content_utils_kind": None,
        "content_utils_screen": None,
        "content_utils_panel_chat_id": None,
        "content_utils_panel_message_id": None,
        "awaiting_content_utils_media": None,
        "awaiting_admin_add_id": False,
        "awaiting_admin_add_code": False,
        "awaiting_block_reason_for_code": None,
        "awaiting_profile_comment_code": None,
        "awaiting_profile_edit_code": None,
        "awaiting_profile_edit_field": None,
    }


async def _save_admin_utils_state(container: AppContainer, session, state: dict) -> None:
    payload = dict(session.state_data)
    payload["_admin_utils"] = {
        "awaiting_payment_text": bool(state.get("awaiting_payment_text")),
        "awaiting_payment_media": bool(state.get("awaiting_payment_media")),
        "awaiting_backup_target": bool(state.get("awaiting_backup_target")),
        "awaiting_payment_review_target": bool(state.get("awaiting_payment_review_target")),
        "awaiting_prohibited_text": bool(state.get("awaiting_prohibited_text")),
        "awaiting_prohibited_media": bool(state.get("awaiting_prohibited_media")),
        "awaiting_delivery_text": bool(state.get("awaiting_delivery_text")),
        "awaiting_delivery_media": bool(state.get("awaiting_delivery_media")),
        "awaiting_contacts_text": bool(state.get("awaiting_contacts_text")),
        "awaiting_contacts_media": bool(state.get("awaiting_contacts_media")),
        "awaiting_profile_search_query": bool(state.get("awaiting_profile_search_query")),
        "profile_search_mode": state.get("profile_search_mode"),
        "awaiting_block_search_query": bool(state.get("awaiting_block_search_query")),
        "block_search_mode": state.get("block_search_mode"),
        "block_operation": state.get("block_operation"),
        "awaiting_codes_add": bool(state.get("awaiting_codes_add")),
        "awaiting_codes_remove": bool(state.get("awaiting_codes_remove")),
        "awaiting_faq_media_section_id": state.get("awaiting_faq_media_section_id"),
        "faq_admin_screen": state.get("faq_admin_screen"),
        "faq_admin_nav_section_id": state.get("faq_admin_nav_section_id"),
        "faq_admin_pick_nav_section_id": state.get("faq_admin_pick_nav_section_id"),
        "faq_admin_target_section_id": state.get("faq_admin_target_section_id"),
        "faq_admin_panel_chat_id": state.get("faq_admin_panel_chat_id"),
        "faq_admin_panel_message_id": state.get("faq_admin_panel_message_id"),
        "content_utils_kind": state.get("content_utils_kind"),
        "content_utils_screen": state.get("content_utils_screen"),
        "content_utils_panel_chat_id": state.get("content_utils_panel_chat_id"),
        "content_utils_panel_message_id": state.get("content_utils_panel_message_id"),
        "awaiting_content_utils_media": state.get("awaiting_content_utils_media"),
        "awaiting_admin_add_id": bool(state.get("awaiting_admin_add_id")),
        "awaiting_admin_add_code": bool(state.get("awaiting_admin_add_code")),
        "awaiting_block_reason_for_code": state.get("awaiting_block_reason_for_code"),
        "awaiting_profile_comment_code": state.get("awaiting_profile_comment_code"),
        "awaiting_profile_edit_code": state.get("awaiting_profile_edit_code"),
        "awaiting_profile_edit_field": state.get("awaiting_profile_edit_field"),
    }
    session.state_data = payload
    await container.session_repo.save(session)


def admin_session_has_pending(session) -> bool:
    if admin_utils_has_waiter(_get_admin_utils_state(session)):
        return True
    if _get_admin_broadcast_state(session).get("awaiting_payload"):
        return True
    orders_state = _get_admin_orders_state(session)
    if (
        orders_state.get("edit_field")
        or orders_state.get("bulk_field")
        or orders_state.get("pending_field")
        or orders_state.get("awaiting_order_search_query")
    ):
        return True
    return False


async def clear_admin_input_states(container: AppContainer, session) -> None:
    utils_state = _get_admin_utils_state(session)
    _reset_admin_utils_waiters(utils_state)
    await _save_admin_utils_state(container, session, utils_state)
    broadcast_state = _get_admin_broadcast_state(session)
    broadcast_state["awaiting_payload"] = False
    broadcast_state["audience"] = None
    await _save_admin_broadcast_state(container, session, broadcast_state)
    orders_state = _get_admin_orders_state(session)
    orders_state["edit_order"] = None
    orders_state["edit_field"] = None
    orders_state["bulk_field"] = None
    orders_state["pending_field"] = None
    orders_state["pending_value"] = None
    orders_state["awaiting_order_search_query"] = False
    orders_state["order_search_mode"] = None
    await _save_admin_orders_state(container, session, orders_state)


async def _clear_admin_input_states(container: AppContainer, session) -> None:
    await clear_admin_input_states(container, session)


def _reset_admin_utils_waiters(state: dict) -> None:
    keys = [
        "awaiting_payment_text",
        "awaiting_payment_media",
        "awaiting_backup_target",
        "awaiting_payment_review_target",
        "awaiting_prohibited_text",
        "awaiting_prohibited_media",
        "awaiting_delivery_text",
        "awaiting_delivery_media",
        "awaiting_contacts_text",
        "awaiting_contacts_media",
        "awaiting_profile_search_query",
        "awaiting_block_search_query",
        "awaiting_codes_add",
        "awaiting_codes_remove",
        "awaiting_admin_add_id",
        "awaiting_admin_add_code",
    ]
    for key in keys:
        state[key] = False
    state["profile_search_mode"] = None
    state["block_search_mode"] = None
    state["block_operation"] = None
    reset_faq_admin_state(state)
    reset_content_utils_state(state)
    state["awaiting_block_reason_for_code"] = None
    state["awaiting_profile_comment_code"] = None
    state["awaiting_profile_edit_code"] = None
    state["awaiting_profile_edit_field"] = None

