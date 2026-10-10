"""Freeze the Agent4Drone API configuration used by the baseline.

This script edits only the VM-side Agent4Drone configuration and service
metadata.  It never writes an API key; the key must be supplied through
``LLM_API_KEY`` (or the provider-specific environment used by the service).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


MODEL = "deepseek-v4-flash"
PROVIDER = "Deepseek API"
BASE_URL = "https://api.deepseek.com"
TEMPERATURE = 0.0
# Keep the baseline bounded and reproducible.  This is a tool-step budget (not
# an API retry count); a bounded budget is essential for missions whose
# projected waypoints cannot all be reached in the simulator episode budget.
MAX_ITERATIONS = 12
PROMPT_VERSION = "agent4drone_system_prompt_v1"
BACKUP_NAME = "llm_settings.json.bak_before_deepseek_v4_flash"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configure(root: Path) -> dict[str, Any]:
    settings_path = root / "llm_settings.json"
    prompt_path = root / "template" / "system_prompt.py"
    if not settings_path.is_file():
        raise FileNotFoundError(settings_path)
    if not prompt_path.is_file():
        raise FileNotFoundError(prompt_path)

    backup_path = root / BACKUP_NAME
    if not backup_path.exists():
        shutil.copy2(settings_path, backup_path)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    providers = settings.setdefault("provider_configs", {})
    provider = providers.setdefault(PROVIDER, {})
    provider["type"] = "openai-compatible"
    provider["base_url"] = BASE_URL
    provider["default_model"] = MODEL
    provider["system_prompt"] = "Agent4Drone system prompt is defined by template/system_prompt.py"

    # Keep the API key empty in the file.  initialize_agent_from_settings()
    # will resolve LLM_API_KEY from the process environment at service start.
    provider["api_key"] = ""
    settings["selected_provider"] = PROVIDER
    settings["temperature"] = TEMPERATURE
    settings["max_iterations"] = MAX_ITERATIONS
    settings["toolchain_json_recording"] = True
    settings["experiment_protocol"] = {
        "protocol_version": "agent4drone_baseline_v1",
        "provider": PROVIDER,
        "model": MODEL,
        "base_url": BASE_URL,
        "temperature": TEMPERATURE,
        "max_iterations": MAX_ITERATIONS,
        "prompt_version": PROMPT_VERSION,
        "prompt_file": "template/system_prompt.py",
        "prompt_sha256": sha256_file(prompt_path),
        "split_seed": 20260823,
        "api_key_source": "environment:LLM_API_KEY",
        "toolchain_json_recording": True,
    }
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    service_path = root / "agent_api_service.py"
    service = service_path.read_text(encoding="utf-8")
    old = "        temperature=0.1,\n"
    new = "        temperature=float(settings.get(\"temperature\", 0.0)),\n"
    if old in service:
        service = service.replace(old, new, 1)
    elif new not in service:
        raise RuntimeError("agent_api_service.py temperature line was not found")
    if "agent.max_iterations = int(settings.get(\"max_iterations\", 150))" not in service:
        service = service.replace("    return UAVControlAgent(\n", "    agent = UAVControlAgent(\n", 1)
        marker = "        toolchain_json_recording=toolchain_json_recording,\n    )\n"
        replacement = marker + "    agent.max_iterations = int(settings.get(\"max_iterations\", 150))\n    return agent\n"
        if marker not in service:
            raise RuntimeError("agent_api_service.py agent constructor marker was not found")
        service = service.replace(marker, replacement, 1)
    service_path.write_text(service, encoding="utf-8")

    agent_path = root / "uav_agent.py"
    agent = agent_path.read_text(encoding="utf-8")
    old_init = "        self.llm_model = llm_model\n"
    new_init = "        self.llm_model = llm_model\n        self.temperature = temperature\n"
    if new_init not in agent:
        if old_init not in agent:
            raise RuntimeError("uav_agent.py llm_model assignment was not found")
        agent = agent.replace(old_init, new_init, 1)
    marker = '            "model": getattr(self, "llm_model", None),\n'
    addition = marker + '            "temperature": getattr(self, "temperature", None),\n            "prompt_version": "agent4drone_system_prompt_v1",\n'
    if '            "prompt_version": "agent4drone_system_prompt_v1",\n' not in agent:
        if marker not in agent:
            raise RuntimeError("uav_agent.py runtime metadata marker was not found")
        agent = agent.replace(marker, addition, 1)
    agent_path.write_text(agent, encoding="utf-8")

    return {
        "provider": PROVIDER,
        "model": MODEL,
        "base_url": BASE_URL,
        "temperature": TEMPERATURE,
        "max_iterations": MAX_ITERATIONS,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": sha256_file(prompt_path),
        "toolchain_json_recording": True,
        "backup": str(backup_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("~/uav_exp/MultiUAV-Plat/agent4drone").expanduser())
    args = parser.parse_args()
    result = configure(args.root.expanduser().resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
