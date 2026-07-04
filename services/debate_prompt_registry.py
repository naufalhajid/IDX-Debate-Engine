from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


PROMPT_DIR = Path(__file__).with_name("debate_prompts")
MANIFEST_PATH = PROMPT_DIR / "manifest.json"

RUNTIME_REQUIRED_PROMPTS: dict[str, str] = {
    "FUNDAMENTAL_SCOUT_PROMPT": "fundamental_scout.txt",
    "CHARTIST_PROMPT": "chartist.txt",
    "SENTIMENT_PROMPT": "sentiment.txt",
    "BULL_SYSTEM_PROMPT_R1": "bull_r1.txt",
    "BULL_SYSTEM_PROMPT_R2": "bull_r2.txt",
    "BEAR_SYSTEM_PROMPT_R1": "bear_r1.txt",
    "BEAR_SYSTEM_PROMPT_R2": "bear_r2.txt",
    "DEVILS_ADVOCATE_PROMPT": "devils_advocate.txt",
    "CIO_SYSTEM_PROMPT": "cio_judge.txt",
    "AGENT_SIGNAL_PROMPT": "agent_signal.txt",
}

ARCHIVED_PROMPTS: dict[str, str] = {
    "CONSENSUS_PROMPT": "consensus.txt",
    "STATE_CLEANER_PROMPT": "state_cleaner.txt",
}

# Backward-compatible name for callers that ask which prompts block startup.
REQUIRED_PROMPTS = RUNTIME_REQUIRED_PROMPTS


@dataclass(frozen=True)
class PromptRegistry:
    prompt_version: str
    prompts: dict[str, str]
    archived_prompts: dict[str, str]


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Missing debate prompt manifest: {MANIFEST_PATH}")
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid debate prompt manifest JSON: {MANIFEST_PATH}"
        ) from exc


def _manifest_prompts(
    manifest: dict,
    section_name: str,
    defaults: dict[str, str],
) -> dict[str, str]:
    uses_section_schema = (
        "runtime_required_prompts" in manifest or "archived_prompts" in manifest
    )
    section = manifest.get(section_name)
    if isinstance(section, dict):
        return {key: str(filename) for key, filename in section.items()}
    if uses_section_schema:
        return {}

    legacy_prompts = manifest.get("prompts")
    if isinstance(legacy_prompts, dict):
        return {
            key: str(legacy_prompts.get(key, filename))
            for key, filename in defaults.items()
            if key in legacy_prompts or section_name == "runtime_required_prompts"
        }

    return dict(defaults) if section_name == "runtime_required_prompts" else {}


def _read_prompt_file(prompt_name: str, filename: str) -> str:
    path = PROMPT_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"{prompt_name}:{path}")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError(f"{prompt_name}:{path}")
    return content


def load_prompt_registry() -> PromptRegistry:
    manifest = _load_manifest()
    prompt_version = str(manifest.get("prompt_version") or "").strip()
    if not prompt_version:
        raise ValueError("debate prompt manifest requires non-empty prompt_version")

    prompts: dict[str, str] = {}
    archived_prompts: dict[str, str] = {}
    missing: list[str] = []
    empty: list[str] = []

    runtime_manifest_prompts = _manifest_prompts(
        manifest,
        "runtime_required_prompts",
        RUNTIME_REQUIRED_PROMPTS,
    )
    missing_runtime_names = sorted(
        set(RUNTIME_REQUIRED_PROMPTS) - set(runtime_manifest_prompts)
    )
    if missing_runtime_names:
        raise ValueError(
            "Manifest missing required runtime prompts: "
            + ", ".join(missing_runtime_names)
        )

    for key, filename in runtime_manifest_prompts.items():
        try:
            prompts[key] = _read_prompt_file(key, filename)
        except FileNotFoundError as exc:
            missing.append(str(exc))
        except ValueError as exc:
            empty.append(str(exc))

    if missing:
        raise FileNotFoundError(
            "Missing required runtime debate prompt files: " + ", ".join(missing)
        )
    if empty:
        raise ValueError("Empty required runtime debate prompt files: " + ", ".join(empty))

    archive_manifest_prompts = _manifest_prompts(
        manifest,
        "archived_prompts",
        ARCHIVED_PROMPTS,
    )
    for key, filename in archive_manifest_prompts.items():
        path = PROMPT_DIR / filename
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        if content.strip():
            archived_prompts[key] = content

    return PromptRegistry(
        prompt_version=prompt_version,
        prompts=prompts,
        archived_prompts=archived_prompts,
    )


PROMPT_REGISTRY = load_prompt_registry()
PROMPT_VERSION = PROMPT_REGISTRY.prompt_version
