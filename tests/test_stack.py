#!/usr/bin/env python3
"""GL's OWN cellular stack against the model (`mudi-emu up`): what GL reports is GL's code reading
the model's replies. One run walks through several radio pictures by live updates:

  1. dual-sim: SIM 1 SA n71, SIM 2 LTE B66 -> GL samples both; AMBR per slot in sim status
  2. SIM 1 switched live to EN-DC (B66 anchor + n78)  -> rows 4 + 51, NR leg without SINR
  3. SIM 1 loses service                               -> one sentinel row, network_type 0
  4. SIM 1 back on SA, then GL's own band write with an empty NR-SA list (glc
     modem.set_band_config): GL sends nr5g_disable_mode 1, the model falls back to LTE, and
     GL's sampler sees LTE -- the loop through GL's band engine, closed without a radio.

Needs the firmware (mudi-emu fetch && mudi-emu extract), root and qemu binfmt; skips otherwise."""
import json, os, shutil, subprocess, sys, tempfile, textwrap, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CLI = os.path.join(REPO, "bin", "mudi-emu")
SCEN = os.path.join(REPO, "modem", "scenarios")


def can_run():
    if os.geteuid() != 0:
        return False
    r = subprocess.run([sys.executable, "-I", CLI, "check"], capture_output=True, text=True)
    return r.returncode == 0 and "GL stack (up):  ok" in r.stdout


INNER = textwrap.dedent(r'''
    import json, os, subprocess, sys, time
    W = os.environ["MUDI_EMU_WORK"]
    SCEN = sys.argv[1]
    def emu(*a):
        return subprocess.run(["mudi-emu"] + list(a), capture_output=True, text=True)
    def ubus(obj, m, args):
        out = emu("ubus", "-t", "20", "call", obj, m, json.dumps(args)).stdout
        return json.loads(out) if out.strip() else None
    def newest(slot):
        r = ubus("cellular.collect", "get_signals", {"time": 10, "slot": slot}) or {}
        s = [x for x in r.get("signals", []) if x.get("slot") == slot]
        return s[-1]["signal"] if s else None
    def settle():
        time.sleep(22)                                   # GL samples every 10 s
    sc = lambda n: json.load(open(os.path.join(SCEN, n + ".json")))
    out = {}
    assert emu("wait-ready", "120").returncode == 0, "GL never sampled"
    settle()
    out["p1"] = {"s1": newest(1), "s2": newest(2), "status": ubus("cellular.sim", "status", {})}
    nsa = sc("nsa")["subs"]["1"]
    emu("modem", json.dumps({"subs": {"1": {k: nsa[k] for k in ("serving", "anchor", "fallback")}}}))
    settle()
    out["p2"] = {"s1": newest(1), "cell": ubus("cellular.network", "cell_info", {"bus": "cpu", "slot": 1})}
    emu("modem", json.dumps({"subs": {"1": {"serving": {"rat": "NONE"}}}}))
    settle()
    out["p3"] = {"s1": newest(1)}
    sa = sc("sa")["subs"]["1"]
    emu("modem", json.dumps({"subs": {"1": {"serving": sa["serving"], "fallback": sa["fallback"], "anchor": None}}}))
    settle()
    out["p4a"] = {"s1": newest(1)}
    mark = sum(1 for _ in open(os.path.join(W, "at.jsonl")))
    lte = [2, 4, 5, 7, 12, 13, 14, 17, 25, 26, 29, 30, 38, 41, 42, 43, 48, 66, 71]
    nr = [2, 5, 7, 12, 13, 14, 25, 26, 29, 30, 38, 41, 48, 66, 70, 71, 77, 78]
    r = emu("glc", "modem", "set_band_config", json.dumps({"bus": "cpu", "slot": 1, "band_enable": True,
            "network_mode": "AUTO", "band_list": {"LTE": lte, "NR-NSA": nr, "NR-SA": []}}))
    out["write"] = {"rc": r.returncode, "out": r.stdout[-400:]}
    settle()
    out["p4b"] = {"s1": newest(1)}
    out["at_after_write"] = [json.loads(l) for i, l in enumerate(open(os.path.join(W, "at.jsonl"))) if i >= mark]
    json.dump(out, open(os.path.join(W, "result.json"), "w"))
''')


@unittest.skipUnless(can_run(), "needs root, the firmware and qemu binfmt (mudi-emu check)")
class GLAgainstTheModel(unittest.TestCase):
    def test_walk(self):
        w = tempfile.mkdtemp(prefix="mudi-emu-test-", dir=os.path.join(REPO, "work") if os.path.isdir(os.path.join(REPO, "work")) else None)
        self.addCleanup(shutil.rmtree, w, True)
        inner = os.path.join(w, "inner.py")
        with open(inner, "w") as f:
            f.write(INNER)
        p = subprocess.run([sys.executable, "-I", CLI, "up", w, "--scenario", os.path.join(SCEN, "dual-sim.json"),
                            "--", sys.executable, "-I", inner, SCEN], capture_output=True, text=True, timeout=600)
        self.assertEqual(p.returncode, 0, p.stdout[-2000:] + p.stderr[-2000:])
        with open(os.path.join(w, "result.json")) as f:
            r = json.load(f)
        row = lambda rows, *k: [tuple(x[i] for i in k) for x in rows]

        # 1. both SIMs, each with its own picture; AMBR from the per-RAT AT replies
        self.assertEqual(row(r["p1"]["s1"], "network_type", "band", "pci", "rsrp", "rsrq", "sinr"), [(5, 71, 721, -99, -13, 4)])
        self.assertEqual(row(r["p1"]["s2"], "network_type", "band", "pci", "rsrp"), [(4, 66, 136, -117)])
        st = {x["slot"]: x for x in r["p1"]["status"]["sims"]}
        self.assertEqual((st[1]["ambr_dl"], st[1]["ambr_ul"]), (600, 100))
        self.assertEqual((st[2]["ambr_dl"], st[2]["ambr_ul"]), (600, 100))

        # 2. EN-DC: the LTE anchor (4) + the NR leg (51); GL takes no SINR from an SCC line
        s1 = r["p2"]["s1"]
        self.assertEqual(row(s1, "network_type", "band", "pci", "bandwidth"), [(4, 66, 136, 20.0), (51, 78, 516, 100.0)])
        self.assertEqual((s1[0]["rsrp"], s1[0]["sinr"]), (-95, 12), "PCC from QENG")
        self.assertEqual((s1[1]["rsrp"], s1[1]["rsrq"], s1[1]["sinr"]), (-90, -11, -32768))
        self.assertEqual(r["p2"]["cell"]["network_type"], 51)

        # 3. no service: GL's one sentinel row
        self.assertEqual(row(r["p3"]["s1"], "network_type", "rsrp"), [(0, -32768)])

        # 4. GL's band engine through the model: empty NR-SA -> nr5g_disable_mode 1 -> LTE
        self.assertEqual(row(r["p4a"]["s1"], "network_type", "band"), [(5, 71)])
        self.assertEqual(r["write"]["rc"], 0, r["write"])
        sent = [(x["cmd"], x["sub"]) for x in r["at_after_write"]]
        self.assertIn(('AT+QNWPREFCFG="nr5g_disable_mode",1', 1), sent, sent)
        self.assertFalse(any(c.startswith("AT+QNWPREFCFG") and s != 1 for c, s in sent), "slot 1 only")
        self.assertEqual(row(r["p4b"]["s1"], "network_type", "band", "pci"), [(4, 66, 136)])
        self.assertFalse([x for x in r["at_after_write"] if x["source"] == "unmodelled"],
                         "every command GL sent for the write is modelled")


if __name__ == "__main__":
    unittest.main()
