#!/usr/bin/env python3
"""
Shared logic for axonhub-config skill (server-side).
Single source of truth for: GraphQL client, channel config, constants.
"""

import json
import subprocess
from typing import Optional

# ---------------------------------------------------------------------------
# Channel configuration (unified)
# ---------------------------------------------------------------------------

CHANNELS = {
    4: {"name": "Ali-Coding",   "weight": 10, "tags": ["quota6000"],  "quota": 6000,  "billing": "count"},
    3: {"name": "Sensenova",    "weight": 9,  "tags": ["quota1500"],  "quota": 1500,  "billing": "count"},
    6: {"name": "opencode-go",  "weight": 8,  "tags": ["quota3000"],  "quota": 45300, "billing": "token", "peak": "09:00-12:00,14:00-18:00"},
    7: {"name": "opencode-luna","weight": 7,  "tags": ["quota2000"],  "quota": 2000,  "billing": "token", "peak": "09:00-12:00,14:00-18:00"},
    2: {"name": "GLM",          "weight": 3,  "tags": ["quota80"],    "quota": 80,    "billing": "token", "peak": "14:00-18:00"},
    5: {"name": "Ali-Token",    "weight": 2,  "tags": ["quota100"],   "quota": 100,   "billing": "token", "peak": "08:00-22:00"},
    8: {"name": "deepseek",     "weight": 1,  "tags": ["quota0"],     "quota": 0,     "billing": "token", "peak": "09:00-12:00,14:00-18:00"},
}

# Default channel for opencode-go model associations (apply_mapping)
DEFAULT_CHANNEL_ID = 7

# Night-only models for Ali-Token (22:00-08:00) — priority boost in configure_models
ALI_TOKEN_NIGHT_MODELS = {"deepseek-v4-pro-0813", "qwen3.8-max"}

# ---------------------------------------------------------------------------
# GraphQL client
# ---------------------------------------------------------------------------

def fetch_graphql(axonhub_url: str, token: str, query: str, variables: Optional[dict] = None) -> dict:
    """Execute GraphQL query against AxonHub admin API."""
    cmd = [
        "curl", "-s", "-X", "POST", f"{axonhub_url}/admin/graphql",
        "-H", "Content-Type: application/json",
        "-H", f"Authorization: Bearer {token}",
        "-d", json.dumps({"query": query, "variables": variables or {}}),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"curl failed: {result.stderr}")
    return json.loads(result.stdout)
