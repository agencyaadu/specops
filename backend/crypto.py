from cryptography.fernet import Fernet
import os
import base64
import hashlib
import hmac

def _fernet():
    key = os.environ["ENCRYPTION_KEY"]
    # derive a 32-byte url-safe base64 key from whatever string is in env
    hashed = hashlib.sha256(key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(hashed))

def encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()

def decrypt(token: str) -> str:
    if not token:
        return ""
    return _fernet().decrypt(token.encode()).decode()

def hash_pan(pan: str) -> str:
    """Deterministic keyed hash for dedupe/lookups (not reversible)."""
    key = os.environ["ENCRYPTION_KEY"].encode()
    return hmac.new(key, pan.upper().encode(), hashlib.sha256).hexdigest()
