import os
import httpx
from datetime import datetime
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

webhook_router = APIRouter()

class WebhookPayload(BaseModel):
    callSid: str
    phone: str
    name: str | None = None
    product: str | None = None
    order_id: str | None = None
    status: str
    transcript: str | None = None

async def sendWebhook(payload: dict):
    url = os.getenv("ORDER_STATUS_WEBHOOK_URL")
    if not url:
        print("No ORDER_STATUS_WEBHOOK_URL configured. Payload:", payload)
        return
    
    payload["timestamp"] = datetime.utcnow().isoformat() + "Z"
    
    async with httpx.AsyncClient() as client:
        for attempt in range(3):
            try:
                resp = await client.post(url, json=payload, timeout=10.0)
                resp.raise_for_status()
                print(f"Webhook delivered successfully: {payload.get('status')}")
                return
            except Exception as e:
                print(f"Webhook retry {attempt + 1}/3 failed: {e}")
                await asyncio.sleep(2)

@webhook_router.post("/api/webhook/dispatch")
async def dispatch_webhook(payload: WebhookPayload):
    """Manual trigger to dispatch webhook."""
    await sendWebhook(payload.model_dump())
    return {"status": "dispatched"}

