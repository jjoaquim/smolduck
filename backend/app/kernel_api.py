"""HTTP/WebSocket surface for the Python kernel — all gated by `sandbox`.

Kept separate from `kernel.py` so the spawned worker process (which imports
`app.kernel`) never has to import FastAPI/Starlette.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .kernel import DEFAULT_TIMEOUT_S, get_kernel
from .sandbox import kernel_disabled_reason, kernel_enabled
from .state import AppState, get_state

# No prefix (mirrors query.py) so the WebSocket can live at /ws/kernel.
router = APIRouter(tags=["kernel"])


class ExecRequest(BaseModel):
    code: str
    timeout: float | None = None


def _require_kernel() -> None:
    if not kernel_enabled():
        raise HTTPException(status_code=403, detail=kernel_disabled_reason())


@router.get("/api/kernel/status")
def kernel_status() -> dict:
    """Capability probe (never gated) so the UI knows whether to offer execution."""
    enabled = kernel_enabled()
    return {"enabled": enabled, "reason": None if enabled else kernel_disabled_reason()}


@router.post("/api/kernel/exec")
def kernel_exec(req: ExecRequest, state: AppState = Depends(get_state)) -> dict:
    _require_kernel()
    if not req.code.strip():
        return {"stdout": "", "stderr": "", "figures": [], "dataframe": None,
                "result": None, "error": None, "timed_out": False, "elapsed_ms": 0.0}
    timeout = req.timeout if req.timeout and req.timeout > 0 else DEFAULT_TIMEOUT_S
    return get_kernel(state).run_collect(req.code, timeout=timeout)


@router.post("/api/kernel/restart")
def kernel_restart(state: AppState = Depends(get_state)) -> dict:
    _require_kernel()
    get_kernel(state).restart()
    return {"restarted": True}


@router.websocket("/ws/kernel")
async def ws_kernel(ws: WebSocket) -> None:
    await ws.accept()
    if not kernel_enabled():
        await ws.send_json({"t": "error", "error": kernel_disabled_reason()})
        await ws.send_json({"t": "done"})
        await ws.close()
        return

    state: AppState = ws.app.state.smol
    km = get_kernel(state)
    try:
        req = await ws.receive_json()
    except Exception:
        await ws.close()
        return

    code = (req.get("code") or "") if isinstance(req, dict) else ""
    timeout = float(req.get("timeout") or DEFAULT_TIMEOUT_S)
    if timeout <= 0:
        timeout = DEFAULT_TIMEOUT_S

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    def emit(ev: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ev)

    def work() -> None:
        # km.run is blocking and serializes via the kernel lock; run it off-loop.
        try:
            km.run(code, emit, timeout=timeout)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    fut = loop.run_in_executor(None, work)
    try:
        while True:
            ev = await queue.get()
            if ev is sentinel:
                break
            await ws.send_json(ev)
    except WebSocketDisconnect:
        pass
    finally:
        await fut  # the cell keeps running to completion even if the client left
        try:
            await ws.close()
        except RuntimeError:
            pass
