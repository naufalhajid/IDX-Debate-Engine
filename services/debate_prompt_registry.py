from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


PROMPT_DIR = Path(__file__).with_name("debate_prompts")
MANIFEST_PATH = PROMPT_DIR / "manifest.json"

REQUIRED_PROMPTS: dict[str, str] = {
    "FUNDAMENTAL_SCOUT_PROMPT": "fundamental_scout.txt",
    "CHARTIST_PROMPT": "chartist.txt",
    "SENTIMENT_PROMPT": "sentiment.txt",
    "BULL_SYSTEM_PROMPT_R1": "bull_r1.txt",
    "BULL_SYSTEM_PROMPT_R2": "bull_r2.txt",
    "BEAR_SYSTEM_PROMPT_R1": "bear_r1.txt",
    "BEAR_SYSTEM_PROMPT_R2": "bear_r2.txt",
    "CONSENSUS_PROMPT": "consensus.txt",
    "STATE_CLEANER_PROMPT": "state_cleaner.txt",
    "DEVILS_ADVOCATE_PROMPT": "devils_advocate.txt",
    "CIO_SYSTEM_PROMPT": "cio_judge.txt",
    "AGENT_SIGNAL_PROMPT": "agent_signal.txt",
}


@dataclass(frozen=True)
class PromptRegistry:
    prompt_version: str
    prompts: dict[str, str]


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Missing debate prompt manifest: {MANIFEST_PATH}")
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid debate prompt manifest JSON: {MANIFEST_PATH}"
        ) from exc


def load_prompt_registry() -> PromptRegistry:
    manifest = _load_manifest()
    prompt_version = str(manifest.get("prompt_version") or "").strip()
    if not prompt_version:
        raise ValueError("debate prompt manifest requires non-empty prompt_version")

    manifest_prompts = manifest.get("prompts") or {}
    prompts: dict[str, str] = {}
    missing: list[str] = []
    empty: list[str] = []

    for key, default_filename in REQUIRED_PROMPTS.items():
        filename = manifest_prompts.get(key, default_filename)
        path = PROMPT_DIR / filename
        if not path.exists():
            missing.append(f"{key}:{path}")
            continue
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            empty.append(f"{key}:{path}")
            continue
        prompts[key] = content

    if missing:
        raise FileNotFoundError(
            "Missing required debate prompt files: " + ", ".join(missing)
        )
    if empty:
        raise ValueError("Empty required debate prompt files: " + ", ".join(empty))

    return PromptRegistry(prompt_version=prompt_version, prompts=prompts)


PROMPT_REGISTRY = load_prompt_registry()
PROMPT_VERSION = PROMPT_REGISTRY.prompt_version
