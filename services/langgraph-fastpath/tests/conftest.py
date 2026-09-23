import os
import sys
from pathlib import Path

os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("N8N_WEBHOOK_URL", "http://n8n.test/webhook/call-event")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
