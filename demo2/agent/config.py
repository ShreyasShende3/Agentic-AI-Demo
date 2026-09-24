import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

WS_URL = os.environ.get("SEQ_WS_URL", "ws://localhost:3000")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# High enough that a normal 2-player game finishes on its own (win
# condition) well before hitting this - it's a safety cap against a stuck
# game, not a "stop the demo early" knob.
MAX_GAMEPLAY_TURNS = int(os.environ.get("MAX_GAMEPLAY_TURNS", "300"))
BOT_STARTUP_TIMEOUT_S = float(os.environ.get("BOT_STARTUP_TIMEOUT_S", "10"))

RULES_KB_DIR = ROOT_DIR / "agent" / "rules_kb"
CHROMA_DIR = ROOT_DIR / ".chroma"

ENABLE_PHOENIX = os.environ.get("ENABLE_PHOENIX", "1") != "0"
