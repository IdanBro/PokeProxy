from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

app = FastAPI(title="Mock Downstream Service")

received_pokemon: list[dict[str, Any]] = []


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "alive"}


@app.post("/pokemon")
async def receive_pokemon(request: Request) -> dict[str, str]:
    body = await request.json()
    reason = request.headers.get("X-Grd-Reason", "unknown")
    received_pokemon.append({"pokemon": body, "reason": reason})
    return {"status": "received"}


@app.get("/received")
async def get_received() -> list[dict[str, Any]]:
    return received_pokemon


@app.delete("/received")
async def clear_received() -> dict[str, str]:
    received_pokemon.clear()
    return {"status": "cleared"}
