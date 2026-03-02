import os
from supabase import create_client, Client

def get_supabase() -> Client | None:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)

def getOrderByPhone(phone: str):
    sb = get_supabase()
    if not sb:
        return None
    try:
        response = sb.table("orders").select("*").eq("phone", phone).execute()
        if response.data and len(response.data) > 0:
            return response.data[0]
    except Exception as e:
        print("Supabase lookup error:", e)
    return None
