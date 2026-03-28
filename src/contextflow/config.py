"""Configuration loader.

Load from config.yaml + environment variables.
Validate all required fields at startup — fail loud if anything is missing.
Expose a single Settings object that the rest of the app imports.
"""
