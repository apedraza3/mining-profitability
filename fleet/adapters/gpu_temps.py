"""GPU temperatures from the home GPU rigs over SSH, read-only.

Each rig's authorized_keys pins this key to ONE command and to the VM's address, e.g.
  from="192.168.1.129",command="nvidia-smi --query-gpu=index,temperature.gpu,temperature.memory,
  power.draw --format=csv,noheader,nounits",no-port-forwarding,no-X11-forwarding,
  no-agent-forwarding,no-pty ssh-ed25519 ... fleet-temps@mining-dashboard
so whatever the client asks for, the rig only ever prints its temperatures.

fleet.env: GPU_TEMP_HOSTS=name=user@host:format,...   format = nvidia | hiveos
  nvidia: the nvidia-smi CSV above
  hiveos: HiveOS's /run/hive/gpu-stats.json ({"temp": [...], "power": [...], "brand": [...]})
The key and pinned host keys live in ./fleet-ssh (gitignored, never in the image), mounted
read-only into the poller only. Unknown or changed host keys are refused.
"""

import json
import subprocess

from fleet import settings
from fleet.adapters.base import Adapter, SourceError

KEY_DIR = "/app/fleet-ssh"


def parse_hosts(raw: str) -> list[tuple[str, str, str]]:
    out = []
    for item in (raw or "").split(","):
        name, _, target = item.strip().partition("=")
        login, _, fmt = target.rpartition(":")
        if name and login:
            out.append((name.strip(), login.strip(), (fmt or "nvidia").strip().lower()))
    return out


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def parse_nvidia(text: str) -> dict:
    temps, mem, power = [], [], []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        temps.append(_num(parts[1]))
        mem.append(_num(parts[2]) if len(parts) > 2 else None)
        power.append(_num(parts[3]) if len(parts) > 3 else None)
    return {"temps_c": temps, "mem_temps_c": mem, "power_w": power}


def parse_hiveos(text: str) -> dict:
    data = json.loads(text[text.index("{"):]) if "{" in text else {}
    brands = data.get("brand") or []
    keep = [i for i, b in enumerate(brands) if str(b).lower() in ("nvidia", "amd")] or \
        list(range(len(data.get("temp") or [])))

    def pick(key):
        vals = data.get(key) or []
        return [_num(vals[i]) if i < len(vals) else None for i in keep]
    # HiveOS reports memory/junction temperature as "mtemp" or "jtemp" depending on the card
    mem_key = "mtemp" if any(_num(v) for v in data.get("mtemp") or []) else "jtemp"
    return {"temps_c": pick("temp"), "mem_temps_c": pick(mem_key), "power_w": pick("power")}


class GpuTempsAdapter(Adapter):
    name = "gputemps"
    interval_s = 60

    def __init__(self):
        self.hosts = parse_hosts(settings.GPU_TEMP_HOSTS)

    def is_configured(self) -> bool:
        return bool(self.hosts)

    def _run(self, login: str) -> str:
        cmd = ["ssh", "-i", f"{KEY_DIR}/id_ed25519", "-o", "BatchMode=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={KEY_DIR}/known_hosts",
               "-o", "ConnectTimeout=8", "-T", login]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode != 0:
            raise SourceError((res.stderr or f"ssh exit {res.returncode}").strip()[:200])
        return res.stdout

    def poll(self) -> dict:
        accounts, failures = {}, []
        for name, login, fmt in self.hosts:
            try:
                parsed = (parse_hiveos if fmt == "hiveos" else parse_nvidia)(self._run(login))
                temps = [t for t in parsed["temps_c"] if t is not None]
                accounts[f"gputemps:{name}"] = {"name": name, "format": fmt, **parsed,
                                                "max_temp_c": max(temps) if temps else None,
                                                "gpus": len(parsed["temps_c"])}
            except Exception as e:  # one rig down must not hide the others
                failures.append(f"{name}: {e}")
                accounts[f"gputemps:{name}"] = {"name": name, "error": str(e)[:200]}
        if failures and len(failures) == len(self.hosts):
            raise SourceError("; ".join(failures))
        return {"machines": [], "accounts": accounts}
