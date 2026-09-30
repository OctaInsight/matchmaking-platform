"""Server-side encrypted login sessions; browser cookies contain only random IDs."""
import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet

IDLE_HOURS = 8

class SessionStore:
    def __init__(self, db, server_key):
        self.db = db
        material = hashlib.sha256(("octa-login-session-v1:" + server_key).encode()).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(material))

    @staticmethod
    def digest(session_id):
        return hashlib.sha256(session_id.encode()).hexdigest()

    def read(self, session_id):
        if not isinstance(session_id, str) or len(session_id) != 64:
            return None
        now = datetime.now(timezone.utc).isoformat()
        rows = self.db.table("platform_login_sessions").select("user_id,encrypted_tokens").eq(
            "session_digest", self.digest(session_id)
        ).gt("expires_at", now).execute().data or []
        if not rows:
            return None
        return {"user_id": rows[0]["user_id"], "tokens": json.loads(self.cipher.decrypt(rows[0]["encrypted_tokens"].encode()))}

    def save(self, tokens, user_id, session_id=None):
        now = datetime.now(timezone.utc)
        payload = {
            "encrypted_tokens": self.cipher.encrypt(json.dumps(tokens).encode()).decode(),
            "last_activity_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=IDLE_HOURS)).isoformat(),
        }
        if session_id:
            rows = self.db.table("platform_login_sessions").update(payload).eq(
                "session_digest", self.digest(session_id)
            ).eq("user_id", user_id).gt("expires_at", now.isoformat()).execute().data or []
            if not rows:
                raise ValueError("Login session expired or revoked")
        else:
            session_id = secrets.token_hex(32)
            self.db.table("platform_login_sessions").insert({
                **payload, "session_digest": self.digest(session_id), "user_id": user_id,
            }).execute()
        return session_id

    def revoke(self, session_id):
        self.db.table("platform_login_sessions").delete().eq("session_digest", self.digest(session_id)).execute()
