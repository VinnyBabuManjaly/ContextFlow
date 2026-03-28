"""Long-term memory — persistent user facts.

Extract facts from conversations via LLM.
Store as vectorized key-value pairs in Redis.
Retrieve relevant facts by query similarity at session start.
TTL 30 days, confidence scoring, contradiction handling.
"""
