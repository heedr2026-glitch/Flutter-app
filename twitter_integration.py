"""Secure Twitter/X OAuth2 PKCE integration primitives."""
import base64, hashlib, os, secrets, urllib.parse
from datetime import datetime, timezone

AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
SCOPES = "tweet.read tweet.write users.read offline.access"

def now(): return datetime.now(timezone.utc).isoformat()
def config():
    return {"client_id": os.environ.get("TWITTER_CLIENT_ID", "").strip(), "client_secret": os.environ.get("TWITTER_CLIENT_SECRET", "").strip(), "redirect_uri": os.environ.get("TWITTER_REDIRECT_URI", "").strip()}
def configured():
    c=config(); return bool(c["client_id"] and c["client_secret"] and c["redirect_uri"])
def pkce():
    verifier=base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode(); challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(); return verifier,challenge
def authorization_url(state, challenge):
    c=config(); return AUTH_URL+"?"+urllib.parse.urlencode({"response_type":"code","client_id":c["client_id"],"redirect_uri":c["redirect_uri"],"scope":SCOPES,"state":state,"code_challenge":challenge,"code_challenge_method":"S256"})
def migrate(c, postgres=False):
    identity = 'BIGSERIAL PRIMARY KEY' if postgres else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    c.execute(f"CREATE TABLE IF NOT EXISTS social_accounts (id {identity}, organization_id BIGINT NOT NULL, provider TEXT NOT NULL, provider_user_id TEXT NOT NULL, username TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL DEFAULT '', avatar_url TEXT NOT NULL DEFAULT '', access_token_enc TEXT NOT NULL, refresh_token_enc TEXT NOT NULL DEFAULT '', expires_at TEXT, status TEXT NOT NULL DEFAULT 'connected', last_synced_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(organization_id,provider))")
    c.execute(f"CREATE TABLE IF NOT EXISTS social_posts (id {identity}, organization_id BIGINT NOT NULL, social_account_id BIGINT NOT NULL, text TEXT NOT NULL, media_url TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, provider_post_id TEXT NOT NULL DEFAULT '', post_url TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, published_at TEXT)")