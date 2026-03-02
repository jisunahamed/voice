import os
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect

twilio_router = APIRouter()

class OutboundCallRequest(BaseModel):
    to: str

@twilio_router.post("/api/call/outbound")
async def outbound_call(req: OutboundCallRequest):
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_FROM_NUMBER")
    base_url = os.getenv("PUBLIC_BASE_URL")

    if not all([account_sid, auth_token, from_number, base_url]):
        raise HTTPException(status_code=500, detail="Missing Twilio/Base config env vars")

    client = Client(account_sid, auth_token)

    try:
        call = client.calls.create(
            to=req.to,
            from_=from_number,
            url=f"{base_url.rstrip('/')}/twilio/voice"
        )
        return {"callSid": call.sid, "status": call.status}
    except Exception as e:
        print(f"Twilio Call Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@twilio_router.api_route("/twilio/voice", methods=["GET", "POST"])
async def twilio_voice(request: Request):
    response = VoiceResponse()
    base_url = os.getenv("PUBLIC_BASE_URL", f"https://{request.url.hostname}")
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
    
    to_num = "Unknown"
    call_sid = "Unknown"
    if request.method == "POST":
        form_data = await request.form()
        to_num = form_data.get('To', 'Unknown')
        call_sid = form_data.get('CallSid', 'Unknown')
    else:
        to_num = request.query_params.get('To', 'Unknown')
        call_sid = request.query_params.get('CallSid', 'Unknown')
        
    connect = Connect()
    stream = connect.stream(url=f"{ws_url.rstrip('/')}/twilio/media")
    # TwiML Stream parameter for transferring data to websocket stream
    stream.parameter(name="toNum", value=to_num)
    stream.parameter(name="callSid", value=call_sid)
    response.append(connect)
    
    return HTMLResponse(content=str(response), media_type="text/xml")
