"""Redis async client.

Create a single async connection pool at startup.
All modules receive this client as a dependency — none create their own connections.
"""
