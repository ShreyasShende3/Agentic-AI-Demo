import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
CREWAI_LLM_STRING = f"ollama/{OLLAMA_MODEL}"

INVOICES_DIR = ROOT_DIR / "data" / "invoices"
DECISIONS_DIR = ROOT_DIR / "data" / "decisions"
POLICY_KB_DIR = ROOT_DIR / "policy_kb"
CHROMA_DIR = ROOT_DIR / ".chroma"

MEMORY_FILE_PATH = ROOT_DIR / ".mcp_memory" / "memory.jsonl"

# Windows npx is a .cmd shim, which a bare subprocess spawn can't exec
# directly - it must be run through cmd.exe. On other platforms plain
# "npx" works fine.
NPX_COMMAND = ("cmd", ["/c", "npx"]) if os.name == "nt" else ("npx", [])
