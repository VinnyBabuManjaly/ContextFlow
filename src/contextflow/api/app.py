"""FastAPI application.

Lifespan: connect to Redis and create indexes on startup.
Mounts all route modules. Serves static UI files if present.
"""
