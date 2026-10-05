"""Socket.IO server: creates the sio instance and registers all handlers.

Usage in app.py:
    from cptr.socket.main import sio_app
    # mount sio_app as the root ASGI app
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import socketio

from cptr.models import Chat
from cptr.utils.config import check_access, now_ms
from cptr.env import CORS_ALLOWED_ORIGINS

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=CORS_ALLOWED_ORIGINS)

# user_id → set of connected sids
_user_sids: dict[str, set[str]] = {}
# (user_id, chat_id) → visible panel instances
_chat_view_sids: dict[tuple[str, str], set[tuple[str, str]]] = {}


@sio.on("connect")
async def on_connect(sid, environ, auth):
    token = auth.get("token") if auth else None
    if not token:
        # Try cookie fallback
        cookie_header = environ.get("HTTP_COOKIE", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("cptr_session="):
                token = part.split("=", 1)[1]
                break

    client_host = environ.get("REMOTE_ADDR", "127.0.0.1")
    user = check_access(client_host=client_host, jwt_token=token)
    if not user or not user.user_id:
        raise ConnectionRefusedError("unauthorized")

    await sio.save_session(sid, {"user_id": user.user_id})
    _user_sids.setdefault(user.user_id, set()).add(sid)


@sio.on("disconnect")
async def on_disconnect(sid):
    session = await sio.get_session(sid)
    uid = session.get("user_id")
    if uid and uid in _user_sids:
        _user_sids[uid].discard(sid)
        if not _user_sids[uid]:
            del _user_sids[uid]
    for key, views in list(_chat_view_sids.items()):
        views_copy = {view for view in views if view[0] != sid}
        if views_copy:
            _chat_view_sids[key] = views_copy
        else:
            del _chat_view_sids[key]


@sio.on("chat:view")
async def on_chat_view(sid, data):
    """Track which chat panels are currently visible for timeout pausing."""
    session = await sio.get_session(sid)
    user_id = session.get("user_id")
    chat_id = data.get("chat_id") if isinstance(data, dict) else None
    view_id = data.get("view_id") if isinstance(data, dict) else None
    if not user_id or not isinstance(chat_id, str) or not isinstance(view_id, str):
        return

    views = _chat_view_sids.setdefault((user_id, chat_id), set())
    view = (sid, view_id)
    if data.get("visible"):
        views.add(view)
    else:
        views.discard(view)
        if not views:
            del _chat_view_sids[(user_id, chat_id)]


@sio.on("chat:read")
async def on_chat_read(sid, data):
    """Persist a user's explicit read acknowledgement for a chat."""
    session = await sio.get_session(sid)
    user_id = session.get("user_id")
    chat_id = data.get("chat_id") if isinstance(data, dict) else None
    if not user_id or not isinstance(chat_id, str):
        return

    last_read_at = now_ms()
    if await Chat.update_last_read_at(chat_id, user_id, last_read_at):
        from cptr.events import EVENTS, publish_event

        await publish_event(
            EVENTS.CHAT_READ,
            actor={"id": user_id},
            subject_id=chat_id,
            subject_type="chat",
            source="socket",
        )
        await broadcast_chat_read_state(user_id, chat_id, last_read_at)


@sio.on("chat:closed")
async def on_chat_closed(sid, data):
    """Close ("conclude") a chat, or reopen it when `closed` is false."""
    session = await sio.get_session(sid)
    user_id = session.get("user_id")
    chat_id = data.get("chat_id") if isinstance(data, dict) else None
    if not user_id or not isinstance(chat_id, str):
        return

    closed = bool(data.get("closed", True))
    closed_at = now_ms() if closed else None
    if await Chat.set_closed_at(chat_id, user_id, closed_at):
        from cptr.events import EVENTS, publish_event

        await publish_event(
            EVENTS.CHAT_CLOSED if closed else EVENTS.CHAT_REOPENED,
            actor={"id": user_id},
            subject_id=chat_id,
            subject_type="chat",
            source="socket",
        )
        await broadcast_chat_closed_state(user_id, chat_id, closed_at)


async def emit_to_user(user_id: str, data: dict):
    """Send events:chat to all of a user's connected tabs/windows."""
    for sid in list(_user_sids.get(user_id, set())):
        await sio.emit("events:chat", data, to=sid)


async def emit_jobs_changed(user_id: str, workspace: str) -> None:
    """Notify all connected tabs that a workspace's job board changed.

    Emits `jobs_changed` *and* `todos_changed`: the todo routes are a shim over
    `jobs` (Phase 1 of the job-queue design), and the dashboard's todo list is
    still driven by the older event.
    """
    await emit_to_user(user_id, {"type": "jobs_changed", "workspace": workspace})
    await emit_to_user(user_id, {"type": "todos_changed", "workspace": workspace})


async def emit_todos_changed(user_id: str, workspace: str) -> None:
    """Notify all connected tabs that a workspace's todos changed."""
    await emit_jobs_changed(user_id, workspace)


async def emit_workspace_notes_changed(user_id: str, workspace: str) -> None:
    """Notify all connected tabs that a workspace's notes changed.

    An agent can add a note mid-turn, so the dashboard cannot rely on having
    fetched the list when it mounted.
    """
    await emit_to_user(user_id, {"type": "workspace_notes_changed", "workspace": workspace})


async def emit_open_browser(
    user_id: str, url: str, *, chat_id: str = "", workspace: str = "", label: str = ""
) -> None:
    """Ask the user's connected tabs to open a Browser tab at ``url``.

    Used by the ``open_browser`` tool so a chat can show the user a page it just
    built or started.

    ``workspace`` is the chat's own workspace, and that is where the tab goes —
    but opening it is **not** a workspace switch. Switching yanked the user out
    of whatever they were doing to look at a page they had not asked for, and it
    lost their place in the chat they were reading. So the tab is added to that
    workspace's own layout here, on the server, where the workspace's row is the
    thing that owns a tab strip. The client then only has to notice that the tab
    is already there; if the user happens to be *in* that workspace they see it
    immediately, and if they are not, nothing on their screen moves and
    ``notify_background_open`` tells them where it went.

    A home chat has no workspace, and there is no tab strip to add to, so the
    event carries no session and the client falls back to a plain browser window.
    """
    session_id = tab_id = ""
    if workspace:
        session_id, tab_id = await _add_browser_tab_to_workspace(
            user_id, workspace, url, label
        )

    await emit_to_user(
        user_id,
        {
            "type": "open_browser",
            "url": url,
            "chat_id": chat_id,
            "workspace": workspace,
            "workspace_name": Path(workspace).name if workspace else "",
            "label": label,
            "browser_session_id": session_id,
            "tab_id": tab_id,
        },
    )
    if workspace and session_id:
        await emit_to_user(
            user_id,
            {
                "type": "workspace_browser_opened",
                "workspace": workspace,
                "workspace_name": Path(workspace).name if workspace else "",
                "url": url,
                "label": label,
            },
        )


async def _add_browser_tab_to_workspace(
    user_id: str, workspace: str, url: str, label: str
) -> tuple[str, str]:
    """Add a Browser tab to ``workspace``'s persisted layout.

    Returns ``(session_id, tab_id)``, either of which is "" when the tab already
    existed or the write failed.

    Written here rather than in the client because a tab belongs to a workspace's
    stored ``groups``, and a workspace the user is not looking at has no live
    client-side store to add it to. Best effort throughout: this runs inside a
    tool call that has already probed the URL and decided to show it, so failing
    to pre-create the tab must not turn a working page into an error. Return ""
    and the client falls back to opening it where it can.
    """
    from cptr.models import Workspace
    from cptr.utils.browser.proxy import manager

    session_id = ""
    try:
        session = await manager.create(user_id)
        await manager.update(session.session_id, user_id, url=url, title=label or None)
        session_id = session.session_id
    except Exception:
        logging.getLogger(__name__).warning(
            "Could not pre-create a Browser session for %s", workspace, exc_info=True
        )
        return "", ""

    try:
        row = await Workspace.get_by_user_path(user_id, workspace)
        data = dict(row.data or {}) if row else {}
        groups = data.get("groups")
        if not isinstance(groups, list) or not groups:
            groups = [{"id": "default", "tabs": [], "activeTabId": "files"}]

        tab_id = uuid.uuid4().hex[:12]
        tab = {
            "id": tab_id,
            "type": "browser",
            "label": label or url,
            "path": url,
            "browserSessionId": session_id,
        }
        # The first group is the one the pinned Dash/Files/Chat tabs live in, so
        # it is the tab strip the user reads. Fall back to whatever the
        # workspace has rather than dropping the tab on the floor.
        target = next(
            (g for g in groups if isinstance(g, dict) and any(
                t.get("type") in {"dash", "files"} for t in g.get("tabs", []) if isinstance(t, dict)
            )),
            groups[0],
        )
        tabs = target.setdefault("tabs", [])
        # A page already open in this workspace is the same tab; do not stack.
        if any(
            isinstance(t, dict) and t.get("type") == "browser" and t.get("path") == url
            for t in tabs
        ):
            await manager.close(session_id, user_id)
            return "", ""
        tabs.append(tab)
        target["activeTabId"] = tab_id
        data["groups"] = groups
        await Workspace.upsert(
            user_id,
            workspace,
            row.name if row and row.name else Path(workspace).name or workspace,
            data,
        )
        return session_id, tab_id
    except Exception:
        logging.getLogger(__name__).warning(
            "Could not add a Browser tab to workspace %s", workspace, exc_info=True
        )
        await manager.close(session_id, user_id)
        return "", ""


async def broadcast_chat_read_state(user_id: str, chat_id: str, last_read_at: int) -> None:
    """Echo a chat's read watermark plus its workspace unread count."""
    chat = await Chat.get_by_id(chat_id)
    workspace = (chat.meta or {}).get("workspace", "") if chat else ""
    from cptr.utils.chat_task import get_active_chat_ids

    unread_counts = await Chat.unread_counts_by_workspace(
        user_id, [workspace], get_active_chat_ids()
    )
    await emit_to_user(
        user_id,
        {
            "chat_id": chat_id,
            "workspace": workspace,
            "last_read_at": last_read_at,
            "workspace_unread_count": unread_counts.get(workspace, 0),
        },
    )


async def broadcast_chat_closed_state(
    user_id: str, chat_id: str, closed_at: int | None
) -> None:
    """Echo a chat's closed state plus its workspace unread count."""
    chat = await Chat.get_by_id(chat_id)
    workspace = (chat.meta or {}).get("workspace", "") if chat else ""
    last_read_at = chat.last_read_at if chat else None
    from cptr.utils.chat_task import get_active_chat_ids

    unread_counts = await Chat.unread_counts_by_workspace(
        user_id, [workspace], get_active_chat_ids()
    )
    await emit_to_user(
        user_id,
        {
            "chat_id": chat_id,
            "workspace": workspace,
            "closed_at": closed_at,
            "last_read_at": last_read_at,
            "workspace_unread_count": unread_counts.get(workspace, 0),
        },
    )


def is_chat_visible(user_id: str, chat_id: str) -> bool:
    return bool(_chat_view_sids.get((user_id, chat_id)))


def is_user_active(user_id: str) -> bool:
    """Return True when the user has at least one connected app session."""
    return bool(_user_sids.get(user_id))


def get_asgi_app(other_app):
    """Wrap a FastAPI/Starlette app with the Socket.IO ASGI layer."""
    return socketio.ASGIApp(sio, other_asgi_app=other_app)
