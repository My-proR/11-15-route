"""WebSocket access. Clients first POST /realtime/ticket (normal bearer auth) and then connect with the 60s single-use ticket,
so long-lived tokens never appear in URLs. The server only pushes events the connection's role is entitled to."""
import asyncio

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ..auth import Principal, require_roles
from ..config import get_settings
from ..services.realtime import hub

router = APIRouter(tags=["realtime"])


@router.post("/realtime/ticket")
async def ticket(p: Principal = Depends(require_roles("org_admin", "dispatcher", "driver", "employee"))):
    return {"ticket": hub.issue_ticket(p.user_id, p.org_id, p.role), "expires_in": get_settings().realtime_ticket_ttl_seconds}


@router.websocket("/ws")
async def ws(websocket: WebSocket, ticket: str = ""):
    conn = hub.redeem_ticket(ticket)
    if conn is None:
        await websocket.close(code=4401)
        return
    if hub.count_for(conn.user_id) >= get_settings().ws_max_connections_per_user:
        await websocket.close(code=4429)
        return
    await websocket.accept()
    hub.add(conn)
    try:
        async def sender():
            while True:
                await websocket.send_json(await conn.queue.get())

        async def receiver():
            while True:
                msg = await websocket.receive_text()     # inbound messages are ignored (server push only); ping keeps the socket alive
                if msg == "ping":
                    await websocket.send_text("pong")

        done, pending = await asyncio.wait({asyncio.create_task(sender()), asyncio.create_task(receiver())}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(conn)
