"""Networked rack PDUs over SNMPv2c, read-only (never switches outlets).

Configure in fleet.env:  PDU_DEVICES=name=host[@community],name=host[@community]
(community defaults to PDU_SNMP_COMMUNITY, else "public"). SNMP has to be enabled on
each PDU's web UI with a read-only community first.

Vendors, by sysObjectID:
- Chatsworth eConnect (enterprise 30932, CPI-UNITY-MIB; OIDs per the netdata/Datadog
  chatsworth-pdu profile):
  * legacy scalars: branch active power W at .1.1.3.3.{1-6}.0 (XY1 YZ1 ZX1 XY2 YZ2 ZX2),
    current 1/100 A at .1.1.3.1.n.0, voltage 1/10 V at .1.1.3.2.n.0
  * newer table: branch power VA at .1.10.1.3.110.1.6, current 1/100 A at .110.1.3,
    voltage 1/10 V at .110.1.5; whole-PDU power VA at .1.10.1.2.10.1.16
  Active watts are preferred; VA is reported as apparent power when that's all there is.
- APC Switched Rack PDU 2G, e.g. AP8941 (enterprise 318, PowerNet-MIB rPDU2):
  rPDU2DeviceStatusPower .1.3.6.1.4.1.318.1.1.26.4.3.1.5 (1/100 kW),
  PeakPower .6 (1/100 kW), Energy .9 (1/10 kWh, resettable). -1 = unsupported.
"""

import logging
import os

from fleet import snmp
from fleet.adapters.base import Adapter

logger = logging.getLogger(__name__)

SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"

CPI = "1.3.6.1.4.1.30932"
CPI_LEGACY_BRANCHES = ("XY1", "YZ1", "ZX1", "XY2", "YZ2", "ZX2")
APC = "1.3.6.1.4.1.318"
APC_RPDU2_STATUS = "1.3.6.1.4.1.318.1.1.26.4.3.1"


def parse_devices(raw: str, default_community: str) -> list[tuple[str, str, str]]:
    out = []
    for item in (raw or "").split(","):
        item = item.strip()
        if "=" not in item:
            continue
        name, target = item.split("=", 1)
        host, _, community = target.partition("@")
        out.append((name.strip(), host.strip(), community.strip() or default_community))
    return out


def read_chatsworth(host: str, community: str) -> dict:
    n = len(CPI_LEGACY_BRANCHES)
    legacy = snmp.get(host, community,
                      [f"{CPI}.1.1.3.3.{i}.0" for i in range(1, n + 1)]
                      + [f"{CPI}.1.1.3.1.{i}.0" for i in range(1, n + 1)]
                      + [f"{CPI}.1.1.3.2.{i}.0" for i in range(1, n + 1)])
    branches = []
    for i, label in enumerate(CPI_LEGACY_BRANCHES, start=1):
        w = legacy.get(f"{CPI}.1.1.3.3.{i}.0")
        if not isinstance(w, int) or w < 0:
            continue
        amps = legacy.get(f"{CPI}.1.1.3.1.{i}.0")
        volts = legacy.get(f"{CPI}.1.1.3.2.{i}.0")
        branches.append({"branch": label, "watts": w,
                         "amps": amps / 100 if isinstance(amps, int) and amps >= 0 else None,
                         "volts": volts / 10 if isinstance(volts, int) and volts >= 0 else None})
    if branches:
        return {"total_w": sum(b["watts"] for b in branches), "power_kind": "active (W)",
                "branches": branches, "mib": "CPI legacy"}

    # Newer firmware: tables in VA
    va = snmp.walk(host, community, f"{CPI}.1.10.1.3.110.1.6")
    amps = snmp.walk(host, community, f"{CPI}.1.10.1.3.110.1.3")
    volts = snmp.walk(host, community, f"{CPI}.1.10.1.3.110.1.5")
    for oid, value in va.items():
        idx = oid.rsplit(f"{CPI}.1.10.1.3.110.1.6.", 1)[-1]
        a = amps.get(f"{CPI}.1.10.1.3.110.1.3.{idx}")
        v = volts.get(f"{CPI}.1.10.1.3.110.1.5.{idx}")
        branches.append({"branch": idx, "va": value,
                         "amps": a / 100 if isinstance(a, int) and a >= 0 else None,
                         "volts": v / 10 if isinstance(v, int) and v >= 0 else None})
    total = snmp.walk(host, community, f"{CPI}.1.10.1.2.10.1.16")
    total_va = sum(v for v in total.values() if isinstance(v, int) and v >= 0) if total else None
    if total_va is None and branches:
        total_va = sum(b["va"] for b in branches if isinstance(b.get("va"), int))
    return {"total_w": total_va, "power_kind": "apparent (VA)", "branches": branches,
            "mib": "CPI-UNITY"}


def read_apc(host: str, community: str) -> dict:
    values = snmp.walk(host, community, APC_RPDU2_STATUS)

    def col(c):
        return [v for o, v in values.items() if o.startswith(f"{APC_RPDU2_STATUS}.{c}.")
                and isinstance(v, int) and v >= 0]
    power, peak, energy = col(5), col(6), col(9)
    return {
        "total_w": sum(power) * 10 if power else None,          # 1/100 kW -> W
        "peak_w": max(peak) * 10 if peak else None,
        "energy_kwh": sum(energy) / 10 if energy else None,     # 1/10 kWh, resettable
        "power_kind": "active (W)", "branches": [], "mib": "PowerNet rPDU2",
    }


class PduAdapter(Adapter):
    name = "pdu"
    interval_s = 60

    def __init__(self):
        self.devices = parse_devices(os.getenv("PDU_DEVICES", ""),
                                     os.getenv("PDU_SNMP_COMMUNITY", "") or "public")

    def is_configured(self) -> bool:
        return bool(self.devices)

    def poll(self) -> dict:
        accounts, failures = {}, []
        for name, host, community in self.devices:
            try:
                sysinfo = snmp.get(host, community, [SYS_DESCR, SYS_OBJECT_ID, SYS_NAME])
                oid = str(sysinfo.get(SYS_OBJECT_ID) or "")
                if oid.startswith(CPI):
                    data = read_chatsworth(host, community)
                elif oid.startswith(APC):
                    data = read_apc(host, community)
                else:
                    data = {"total_w": None, "power_kind": None, "branches": [],
                            "mib": f"unknown vendor {oid}"}
                accounts[f"pdu:{name}"] = {
                    "name": name, "host": host, "sys_descr": sysinfo.get(SYS_DESCR),
                    "sys_name": sysinfo.get(SYS_NAME), "sys_object_id": oid, **data,
                }
            except Exception as e:  # one PDU down must not hide the other
                logger.warning("PDU %s (%s) failed: %s", name, host, e)
                failures.append(f"{name}: {e}")
                accounts[f"pdu:{name}"] = {"name": name, "host": host, "error": str(e)[:200]}
        if failures and len(failures) == len(self.devices):
            raise RuntimeError("; ".join(failures))
        return {"machines": [], "accounts": accounts}
