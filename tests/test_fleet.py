"""Fleet Now math and parsers. Run: python -m unittest discover -s tests

Stdlib unittest only, so it runs in the app image without extra packages.
"""

import os
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

# Point the store at a throwaway DB before anything imports settings
_TMP = tempfile.mkdtemp()
os.environ["FLEET_DB"] = str(Path(_TMP) / "telemetry.db")
os.environ["FLEET_OVERHEADS"] = str(Path(_TMP) / "none.json")

from fleet import history, snmp, store, view  # noqa: E402
from fleet.adapters import kryptex, lan_asic, pdu, vast  # noqa: E402
from fleet.units import pay_rate_per_hs, to_hs  # noqa: E402


def iso(seconds_ago=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).replace(microsecond=0).isoformat()


def machine(name, watts, quality="estimated (nameplate)", running=True, location="home", expected=10.0):
    row = {"name": name, "kind": "asic", "location": location, "running": running, "rate_kwh": 0.1,
           "expected_usd_day": expected, "watts": watts, "watts_quality": quality}
    view._apply_costs(row, watts, quality, 0.1, 16.41, location == "home")
    return row


class Units(unittest.TestCase):
    def test_to_hs(self):
        self.assertEqual(to_hs(114, "KH/s"), 114e3)
        self.assertEqual(to_hs(420, "KSol/s"), 420e3)
        self.assertEqual(to_hs(200, "TH/s"), 200e12)
        self.assertIsNone(to_hs(1, "furlongs"))

    def test_pay_rate(self):
        self.assertAlmostEqual(pay_rate_per_hs(45.0, "BTC/EH/s"), 45.0 / 1e18)


class LanAsic(unittest.TestCase):
    def test_equihash_ghs_label_is_sols(self):
        # Bitmain Z11/Z15 firmware labels Sol/s as "GHS": 420000 means 420 KSol/s, not 420 TSol/s
        self.assertEqual(lan_asic.parse_hashrate({"GHS 5s": 420000.0}, "Antminer Z15"), 420000.0)

    def test_sha256_ghs(self):
        self.assertEqual(lan_asic.parse_hashrate({"GHS 5s": 200000.0}, "Antminer S21"), 200000e9)


class Kryptex(unittest.TestCase):
    def test_usd_per_hs_day(self):
        v = kryptex.usd_per_hs_day({"net_hashrate": 1e12, "fee": 0.01}, {"daily_emission": 1000}, 2.0)
        self.assertAlmostEqual(v, 1000 / 1e12 * 0.99 * 2.0)
        self.assertIsNone(kryptex.usd_per_hs_day({}, {"daily_emission": 1}, 1.0))


class Vast(unittest.TestCase):
    def test_occupancy_and_totals(self):
        self.assertEqual(vast.rented_gpus("D D x x"), 2)
        self.assertEqual(vast.rented_gpus("x x"), 0)
        self.assertIsNone(vast.rented_gpus(None))
        self.assertEqual(vast.earning_total({"gpu_earn": 1.5, "sto_earn": 0.25, "machine_id": 7}), 1.75)
        self.assertEqual(vast.earning_total({"Total": "3.0", "gpu_earn": 1}), 3.0)

    def test_poll_parses_machines_and_earnings(self):
        responses = {
            "/machines": {"machines": [{"id": 7, "hostname": "gpu-box", "num_gpus": 4,
                                        "gpu_occupancy": "D x x x", "listed_gpu_cost": 0.3}]},
            "/users/me/machine-earnings": {
                "per_machine": [{"machine_id": 7, "gpu_earn": 14.0}],
                "per_day": [{"day": 1, "gpu_earn": 2.0}, {"day": 2, "gpu_earn": 3.0}]},
        }
        with mock.patch.object(vast.settings, "VAST_API_KEY", "k"), \
                mock.patch.object(vast.VastAdapter, "_get", side_effect=lambda path, params: responses[path]):
            out = vast.VastAdapter().poll()
        m = out["machines"][0]
        self.assertEqual((m["machine_key"], m["name"], m["status"]), ("vast:7", "gpu-box", "1/4 GPUs rented"))
        acct = out["accounts"]["vast:earnings"]
        self.assertEqual(acct["last_7d_usd"], 5.0)
        self.assertEqual(acct["per_machine_7d_usd"], {"7": 14.0})


class Snmp(unittest.TestCase):
    def test_oid_roundtrip(self):
        oid = "1.3.6.1.4.1.30932.1.10.1.3.110.1.6.1.12.48"
        tlv = snmp._oid(oid)
        tag, body, _ = snmp._read(tlv, 0)
        self.assertEqual(tag, 0x06)
        self.assertEqual(snmp._decode_oid(body), oid)

    def test_get_against_fake_agent(self):
        """Full request/response over UDP: the client must parse a real GetResponse."""
        agent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        agent.bind(("127.0.0.1", 0))
        port = agent.getsockname()[1]

        def serve():
            data, addr = agent.recvfrom(4096)
            _, top, _ = snmp._read(data, 0)
            i = 0
            _, _, i = snmp._read(top, i)
            _, _, i = snmp._read(top, i)
            _, pdu_body, _ = snmp._read(top, i)
            _, rid, _ = snmp._read(pdu_body, 0)
            vb = snmp._tlv(0x30, snmp._oid("1.3.6.1.2.1.1.5.0") + snmp._tlv(0x04, b"PDU-104-R"))
            vb += snmp._tlv(0x30, snmp._oid("1.3.6.1.4.1.318.1.1.26.4.3.1.5.1") + snmp._tlv(0x42, (270).to_bytes(2, "big")))
            resp_pdu = snmp._tlv(0xA2, snmp._tlv(0x02, rid) + snmp._int(0) + snmp._int(0) + snmp._tlv(0x30, vb))
            agent.sendto(snmp._tlv(0x30, snmp._int(1) + snmp._tlv(0x04, b"public") + resp_pdu), addr)

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        out =snmp._send("127.0.0.1", "public", 0xA0, ["1.3.6.1.2.1.1.5.0"], 2.0, port=port)
        t.join(2)
        agent.close()
        self.assertEqual(dict(out), {"1.3.6.1.2.1.1.5.0": "PDU-104-R",
                                     "1.3.6.1.4.1.318.1.1.26.4.3.1.5.1": 270})


class Pdu(unittest.TestCase):
    def test_parse_devices(self):
        self.assertEqual(pdu.parse_devices("chatsworth=192.168.1.78@secret, apc=192.168.1.30", "public"),
                         [("chatsworth", "192.168.1.78", "secret"), ("apc", "192.168.1.30", "public")])

    def test_read_apc_banks(self):
        base, bank, volts = pdu.APC_RPDU2_STATUS, pdu.APC_RPDU2_BANK, pdu.APC_RPDU2_PHASE_VOLTS
        tables = {
            base: {f"{base}.5.1": 270, f"{base}.6.1": 603, f"{base}.9.1": 1032452},
            bank: {f"{bank}.3.1": 1, f"{bank}.3.2": 2, f"{bank}.5.1": 65, f"{bank}.5.2": 44},
            volts: {f"{volts}.1": 246},
        }
        with mock.patch.object(pdu.snmp, "walk", side_effect=lambda h, c, b: tables.get(b, {})):
            r = pdu.read_apc("x", "public")
        self.assertEqual(r["total_w"], 2700)
        self.assertEqual(r["peak_w"], 6030)
        self.assertAlmostEqual(r["energy_kwh"], 103245.2)
        self.assertEqual([(b["branch"], b["va"]) for b in r["branches"]], [("Bank 1", 1599), ("Bank 2", 1082)])

    def test_read_chatsworth_unity_labels_breakers(self):
        cpi = pdu.CPI
        mac = "12.48.48.48"
        va = {f"{cpi}.1.10.1.3.110.1.6.1.{mac}": 877, f"{cpi}.1.10.1.3.110.1.6.2.{mac}": 2984}
        amps = {f"{cpi}.1.10.1.3.110.1.3.1.{mac}": 358}

        def walk(h, c, b):
            return {f"{cpi}.1.10.1.3.110.1.6": va, f"{cpi}.1.10.1.3.110.1.3": amps}.get(b, {})
        with mock.patch.object(pdu.snmp, "get", return_value={}), \
                mock.patch.object(pdu.snmp, "walk", side_effect=walk):
            r = pdu.read_chatsworth("x", "c")
        self.assertEqual([b["branch"] for b in r["branches"]], ["CB1", "CB2"])
        self.assertEqual(r["total_w"], 3861)
        self.assertEqual(r["branches"][0]["amps"], 3.58)


class InventoryMatch(unittest.TestCase):
    def test_containment(self):
        index = view._inventory_index([{"id": 1, "name": "S21Hosted"}], [])
        self.assertEqual(view._match_inventory(index, "S21Hosted1")["id"], 1)
        self.assertIsNone(view._match_inventory(index, "S21"))  # too short a match


class Reconcile(unittest.TestCase):
    def realtime(self, w, age=10):
        return {"crypto_mining_w": w, "measured_at": iso(age)}

    def test_allocated_within_tolerance(self):
        rows = [machine("A", 1000), machine("B", 1000)]
        info = view.reconcile_home_power(rows, self.realtime(2100), 0.1, 16.41)
        self.assertEqual(info["mode"], "allocated")
        self.assertAlmostEqual(sum(r["watts"] for r in rows), 2100)

    def test_stale_meter_is_ignored(self):
        rows = [machine("A", 1000)]
        info = view.reconcile_home_power(rows, self.realtime(5000, age=3600), 0.1, 16.41)
        self.assertEqual(info["mode"], "meter stale")
        self.assertEqual(len(rows), 1)

    def test_declared_loads_cap_and_non_mining(self):
        rows = [machine("Z15", 1500), machine("Z11", 3500)]
        overheads = {"home": [{"label": "C6 cooling", "serves": ["Z15"], "max_w": 2500},
                              {"label": "Server + workshop", "mining": False}]}
        info = view.reconcile_home_power(rows, self.realtime(9000), 0.1, 16.41, overheads)
        self.assertEqual(info["mode"], "overhead")
        self.assertEqual([(l["label"], l["watts"]) for l in info["loads"]],
                         [("C6 cooling", 2500), ("Server + workshop", 1500)])
        self.assertEqual(info["non_mining_w"], 1500)
        z15 = next(r for r in rows if r["name"] == "Z15")
        cooling_cost = 2.5 * 24 * 0.1 + 2.5 * 16.41 / 30.4
        self.assertAlmostEqual(z15["overhead_usd_day"], cooling_cost)
        server = next(r for r in rows if r["name"] == "Server + workshop")
        self.assertIsNone(server["net_loaded_usd_day"])  # kept out of the mining P&L

    def test_pdu_measured_load_replaces_gap_share(self):
        rows = [machine("Z15", 1500)]
        overheads = {"home": [{"label": "C6 cooling", "serves": ["Z15"], "pdu": "chatsworth:CB1"},
                              {"label": "Server", "mining": False}]}
        readings = {"chatsworth:CB1": {"watts": 900.0, "unit": "VA", "fresh": True}}
        info = view.reconcile_home_power(rows, self.realtime(4000), 0.1, 16.41, overheads, readings)
        self.assertEqual([(l["label"], l["watts"], l["measured"]) for l in info["loads"]],
                         [("C6 cooling", 900, True), ("Server", 1600, False)])


class PduCircuits(unittest.TestCase):
    def test_readings_and_split(self):
        accounts = [{"fetched_at": iso(30), "data": {
            "name": "apc", "total_w": 2700, "power_kind": "active (W)",
            "branches": [{"branch": "Bank 1", "va": 1600}, {"branch": "Bank 2", "va": 1100}]}}]
        readings = view.pdu_circuit_readings(accounts)
        self.assertEqual(readings["apc:total"], {"watts": 2700.0, "unit": "W", "fresh": True})
        rows = [machine("Z11-1", 1200), machine("Z11-2", 400), machine("Off", 1000, running=False)]
        info = view.apply_pdu_circuits(rows, readings, {"APC:bank 1": ["Z11-1", "Z11-2"],
                                                        "apc:Bank 2": ["Off"]}, 0.1, 16.41)
        self.assertEqual(rows[0]["watts"], 1200)  # 1600 x 1200/1600
        self.assertEqual(rows[1]["watts"], 400)
        self.assertTrue(rows[0]["watts_quality"].startswith("measured (PDU apc:Bank 1, VA"))
        self.assertEqual(info[0]["state"], "applied")
        self.assertEqual(info[1]["state"], "no running machine")
        self.assertEqual(info[1]["unassigned_w"], 1100)

    def test_stale_reading_not_applied(self):
        accounts = [{"fetched_at": iso(3600), "data": {"name": "apc", "total_w": 2700, "branches": []}}]
        rows = [machine("A", 1000)]
        info = view.apply_pdu_circuits(rows, view.pdu_circuit_readings(accounts), {"apc:total": ["A"]}, 0.1, 1)
        self.assertEqual(info[0]["state"], "stale")
        self.assertEqual(rows[0]["watts"], 1000)


class RentalRows(unittest.TestCase):
    """Clore/Vast rows through build_fleet, with the inventory supplying location and watts."""

    def test_build_fleet_rental_income_and_location(self):
        state = {
            "sources": [],
            "machines": [
                {"machine_key": "vast:7", "source": "vast", "name": "gpu-box", "kind": "rental_server",
                 "location": None, "running": None, "status": "1/4 GPUs rented",
                 "extra": {"id": 7}, "fetched_at": iso(10)},
                {"machine_key": "clore:rig", "source": "clore", "name": "rig", "kind": "rental_server",
                 "location": "clore", "running": True, "status": "fully rented",
                 "extra": {"rented_price_by_currency": {"bitcoin": 0.0001}}, "fetched_at": iso(10)},
                {"machine_key": "clore:usdrig", "source": "clore", "name": "usdrig", "kind": "rental_server",
                 "location": "clore", "running": True, "status": "fully rented",
                 "extra": {"rented_price_by_currency": {"USD-Blockchain": 22}}, "fetched_at": iso(10)},
            ],
            "accounts": [
                {"account_key": "powerpool:payrates", "source": "powerpool", "fetched_at": iso(10),
                 "data": {"rates": {}, "prices": {"BTC": 100000}}},
                {"account_key": "electricity:rates", "source": "electricity", "fetched_at": iso(10),
                 "data": {"marginal_energy_rate": 0.1, "demand_rate_with_tax": 16.41}},
                {"account_key": "vast:earnings", "source": "vast", "fetched_at": iso(10),
                 "data": {"per_machine_7d_usd": {"7": 14.0}}},
            ],
        }
        inventory = [{"id": 1, "name": "gpu-box", "location_id": "h", "wattage": 1000, "status": "active"}]
        with mock.patch.object(view.store, "read_all", return_value=state):
            f = view.build_fleet(inventory, [{"id": "h", "name": "home"}])
        rows = {r["name"]: r for r in f["machines"]}
        box = rows["gpu-box"]
        self.assertEqual(box["location"], "home")
        self.assertAlmostEqual(box["expected_usd_day"], 2.0)
        self.assertAlmostEqual(box["power_usd_day"], 1.0 * 24 * 0.1)
        self.assertAlmostEqual(rows["rig"]["expected_usd_day"], 10.0)
        self.assertEqual(rows["rig"]["location"], "clore")
        self.assertEqual(rows["usdrig"]["expected_usd_day"], 22.0)


class Solar(unittest.TestCase):
    def test_credit_uses_mining_share_of_house(self):
        realtime = {"solar_w": 3000, "house_w": 11000, "net_grid_w": 8000,
                    "last24h": {"hours": 24, "house_kwh": 250, "crypto_kwh": 216, "solar_kwh": 48}}
        # meter 9 kW of which 1.5 kW is not mining -> mining is 5/6 of the crypto circuits
        s = view.solar_summary(realtime, {"measured_w": 9000, "non_mining_w": 1500}, 0.1)
        self.assertAlmostEqual(s["mining_kwh_24h"], 180.0)
        self.assertAlmostEqual(s["mining_share"], 0.72)
        self.assertAlmostEqual(s["mining_solar_credit_usd_day"], round(48 * 0.72 * 0.1, 2))


class History(unittest.TestCase):
    def test_compact_and_store(self):
        store.init_db()
        fleet = {"summary": {"running": 3, "machines": 4, "expected_usd_day": 80.123, "power_usd_day": 50,
                             "demand_usd_day": 4, "net_loaded_usd_day": 26.1,
                             "home_power": {"measured_w": 9000, "accounted_w": 5000, "non_mining_w": 1500},
                             "solar": {"solar_w": 3000, "house_w": 11000, "mining_solar_credit_usd_day": 3.2},
                             "pdus": [{"name": "apc", "total_w": 2700}, {"name": "bad", "error": "timeout"}]},
                 "machines": [{"kind": "asic", "running": True, "algorithm": "scrypt", "hashrate_hs": 5e9},
                              {"kind": "asic", "running": True, "algorithm": "scrypt", "hashrate_hs": 1e9},
                              {"kind": "overhead", "running": None}]}
        c = history.compact(fleet)
        self.assertEqual(c["expected_usd_day"], 80.12)
        self.assertEqual(c["pdus_w"], {"apc": 2700})
        self.assertEqual(c["hashrate_hs"], {"scrypt": 6e9})
        store.record_snapshot(c, ts=iso(60))
        pts = history.series(1)["points"]
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0]["meter_w"], 9000)

    def test_bucket_average(self):
        t0 = datetime(2026, 11, 9, tzinfo=timezone.utc)
        pts = [{"ts": (t0 + timedelta(minutes=5 * i)).isoformat(), "meter_w": 1000 * i,
                "pdus_w": {"apc": 100 * i}} for i in range(4)]
        out = history._bucket_avg(pts, 600)  # two 10-minute buckets
        self.assertEqual([p["meter_w"] for p in out], [500, 2500])
        self.assertEqual(out[1]["pdus_w"], {"apc": 250})


if __name__ == "__main__":
    unittest.main()
