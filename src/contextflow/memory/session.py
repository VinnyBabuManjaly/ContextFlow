"""Session memory — short-term conversation history.

Redis Streams keyed by session_id.
Append each turn (role, content, timestamp).
Read last N turns for context injection.
MAXLEN ~100 entries, 24h TTL on the key.
"""
