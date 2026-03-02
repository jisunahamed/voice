import os
import json
import asyncio
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from src.db.supabase_client import getOrderByPhone
from src.webhook.dispatch import sendWebhook

realtime_router = APIRouter()

class CallState:
    def __init__(self, phone, call_sid, customer_info):
        self.phone = phone
        self.callSid = call_sid
        self.customer_info = customer_info
        self.transcript = ""
        self.unclear_retries = 0
        self.status = "ongoing"

@realtime_router.websocket("/twilio/media")
async def handle_media_stream(websocket: WebSocket):
    await websocket.accept()

    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-realtime-preview")

    openai_url = f"wss://api.openai.com/v1/realtime?model={openai_model}"

    call_state = CallState(phone="Unknown", call_sid="Unknown", customer_info=None)
    stream_sid = None

    async with websockets.connect(
        openai_url,
        extra_headers={
            "Authorization": f"Bearer {openai_api_key}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        
        async def receive_from_twilio():
            nonlocal stream_sid
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        custom_params = data['start'].get('customParameters', {})
                        call_state.callSid = custom_params.get('callSid', 'Unknown')
                        call_state.phone = custom_params.get('toNum', 'Unknown')
                        
                        print(f"Incoming stream started. toNum={call_state.phone}")
                        
                        # Lookup DB
                        order_info = getOrderByPhone(call_state.phone)
                        call_state.customer_info = order_info
                        
                        name = order_info.get("customer_name") if order_info else None
                        product = order_info.get("ordered_product") if order_info else None
                        
                        if name and product:
                            greeting = f"Sir/Ma'am {name}, you ordered {product}. Do you want to confirm the order?"
                        else:
                            greeting = "We could not find your order. Can I know your name?"
                            # Trigger not_found webhook in background
                            asyncio.create_task(sendWebhook({
                                "callSid": call_state.callSid,
                                "phone": call_state.phone,
                                "name": None,
                                "product": None,
                                "order_id": None,
                                "status": "not_found",
                                "transcript": call_state.transcript
                            }))
                        
                        system_msg = f"""
You are an outbound sales verification assistant. Keep it short, polite, sales-confirmation style.
You must ask only one main question: confirm order yes/no.
Recognize confirmations in Bangla & English. 
Yes mappings: 'হ্যাঁ', 'জি', 'confirm', 'ok', 'ঠিক আছে'
No mappings: 'না', 'না লাগবে', 'cancel', 'না চাই'
If ambiguous: ask 'আপনি কি কনফার্ম করবেন—হ্যাঁ না?' (retry max 2 times).
After final classification, say a quick polite goodbye and stop talking.
"""
                        
                        # Send session update
                        session_update = {
                            "type": "session.update",
                            "session": {
                                "turn_detection": {"type": "server_vad"},
                                "input_audio_format": "g711_ulaw",
                                "output_audio_format": "g711_ulaw",
                                "voice": "alloy",
                                "instructions": system_msg,
                                "modalities": ["text", "audio"],
                                "temperature": 0.6,
                            }
                        }
                        await openai_ws.send(json.dumps(session_update))
                        
                        # Initial prompt
                        user_msg = {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": f"Start the conversation by greeting me exactly with: {greeting}"}]
                            }
                        }
                        await openai_ws.send(json.dumps(user_msg))
                        await openai_ws.send(json.dumps({"type": "response.create"}))
                        
                    elif data['event'] == 'media' and openai_ws.open:
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                        
                    elif data['event'] == 'stop':
                        print("Stream stopped by twilio")
                        
            except WebSocketDisconnect:
                print("Twilio websocket disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            nonlocal stream_sid
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    
                    if response['type'] == 'response.audio.delta' and response.get('delta'):
                        audio_payload = response['delta']
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)
                        
                    elif response['type'] == 'conversation.item.input_audio_transcription.completed':
                        user_text = response.get('transcript', '')
                        call_state.transcript += f"\\nUser: {user_text}"
                        classify_transcript(user_text, call_state)
                        
                    elif response['type'] == 'response.done':
                        item = response.get('response', {}).get('output', [])
                        if item and item[0].get('content'):
                            ai_text = item[0]['content'][0].get('transcript', '')
                            if ai_text:
                                call_state.transcript += f"\\nAI: {ai_text}"
                                
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        def classify_transcript(text, state: CallState):
            if state.status in ["confirmed", "cancelled"]: return
            if not text: return
            
            text_lower = text.lower()
            
            yes_keywords = ['yes', 'confirm', 'ok', 'হ্যাঁ', 'জি', 'ঠিক আছে', 'sure', 'yeah']
            no_keywords = ['no', 'cancel', 'না', 'না লাগবে', 'না চাই', 'nope']
            
            is_yes = any(k in text_lower for k in yes_keywords)
            is_no = any(k in text_lower for k in no_keywords)
            
            if is_yes and not is_no:
                state.status = "confirmed"
            elif is_no:
                state.status = "cancelled"
            else:
                state.unclear_retries += 1
                if state.unclear_retries > 2:
                    state.status = "unclear"
            
            if state.status in ["confirmed", "cancelled", "unclear"]:
                asyncio.create_task(sendWebhook({
                    "callSid": state.callSid,
                    "phone": state.phone,
                    "name": state.customer_info.get("customer_name") if state.customer_info else None,
                    "product": state.customer_info.get("ordered_product") if state.customer_info else None,
                    "order_id": state.customer_info.get("order_id") if state.customer_info else None,
                    "status": state.status,
                    "transcript": state.transcript
                }))

        # Simple timeout functionality (no input for 15s)
        async def handle_timeout():
            while True:
                await asyncio.sleep(15)
                # Just a stub: Could trigger manual check or disconnect.
                if call_state.status != "ongoing":
                    break

        await asyncio.gather(receive_from_twilio(), send_to_twilio(), handle_timeout())
