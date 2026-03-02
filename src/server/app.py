import os
from fastapi import FastAPI
from src.twilio.routes import twilio_router
from src.realtime.websocket import realtime_router
from src.webhook.dispatch import webhook_router
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Outbound Voice Agent")

app.include_router(twilio_router)
app.include_router(realtime_router)
app.include_router(webhook_router)


@app.get("/")
def health_check():
    return {"status": "Server is up and running."}
