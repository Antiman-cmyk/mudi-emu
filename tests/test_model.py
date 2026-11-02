#!/usr/bin/env python3
"""The modem model on its own (no firmware, no root): replies conform to the formats the evidence
names, state is per subscription, prefs steer the serving RAT, live overrides apply.

The FORMATS below are copied from the RG650V-NA baseband (NON-HLOS modem.b24, `strings`) and
from Quectel's apps-side AT processor (libql_atcop.so). A reply that stops matching its format
fails here before anything runs GL against it."""
import json, os, re, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "modem"))
import model  # noqa: E402

SCEN = os.path.join(REPO, "modem", "scenarios")

FORMATS = {
    # modem.b24
    "qcainfo_lte_pcc": '+QCAINFO: "PCC",%lu,%d,"LTE BAND %lu",%d,%d,%d,%d,%d,%d',
    "qcainfo_lte_scc": '+QCAINFO: "SCC",%lu,%d,"LTE BAND %lu",%d,%d,%d,%d,%d,%d,%d,%s,%s',
    "qcainfo_nr_short": '+QCAINFO: "%s",%lu,%d,"NR5G BAND %lu",%d',
    "qcainfo_nr": '+QCAINFO: "%s",%lu,%d,"NR5G BAND %lu",%d,%d,%d,%d',
    "qcainfo_nr_scc_short": '+QCAINFO: "SCC",%lu,%d,"NR5G BAND %lu",%d,%d,%d,%s,%s',
    "qcainfo_nr_scc": '+QCAINFO: "SCC",%lu,%d,"NR5G BAND %lu",%d,%d,%d,%s,%s,%d,%d,%d',
    "qcainfo_mode": "+QCAINFO: %ld",
    # servingcell: '+QENG: "%s","%s","%s","%s",' then '%s,%s,' (mcc, mnc) then the RAT body
    "qeng_sa": '+QENG: "servingcell","%s","NR5G-SA","%s",%s,%s,%09llX,%d,%X,%ld,%d,%d,%s,%s,%s,%d,%s',
    "qeng_lte": '+QENG: "servingcell","%s","LTE","%s",%s,%s,%X,%d,%ld,%d,%d,%d,%X,%s,%s,%s,%s,%d,%d,%s',
    "qeng_head": '+QENG: "servingcell","%s"',
    "qeng_nsa_lte": '+QENG: "LTE","%s",%s,%s,%X,%d,%ld,%d,%d,%d,%X,%s,%s,%s,%s,%d,%d,%s',
    "qeng_nsa_nr": '+QENG: "%s",%s,%s,%d,%s,%s,%s,%ld,%d,%d',
    "ncell_intra": '+QENG: "%s","%s",%ld,%d,%d,%d,%d,%s,%s,%s,%s,%s,%s',
    "ncell_inter": '+QENG: "%s","%s",%ld,%s,%s,%s,%s,%s,%s,%s,%s,%s',
    "lte_ambr": '+QNWCFG: "lte_ambr","%s",%d,%d',
    "nr5g_ambr": '+QNWCFG: "nr5g_ambr","%s",%d,%d,%d,%d',
    "qgdcnt": "+QGDCNT: %llu,%llu",
    # libql_atcop.so
    "qsimtype": '+QSIMTYPE: "slot2",%d',
}
CONV = {"%lu": r"\d+", "%llu": r"\d+", "%ld": r"-?\d+", "%d": r"-?\d+", "%s": r'[^,"]*', "%X": r"[0-9A-F]+",
        "%09llX": r"[0-9A-F]{9,}", "%03ld": r"\d{3,}", "%02ld": r"\d{2,}"}


def rx(fmt):
    out, i = "", 0
    for m in re.finditer(r"%(?:09llX|03ld|02ld|llu|lu|ld|d|s|X)", fmt):
        out += re.escape(fmt[i:m.start()]) + CONV[m.group(0)]
        i = m.end()
    return re.compile(out + re.escape(fmt[i:]) + "$")


def matches(line, name):
    return bool(rx(FORMATS[name]).match(line))


def scen(name):
    with open(os.path.join(SCEN, name + ".json")) as f:
        return json.load(f)


def ask(m, *cmds):
    out = None
    for c in cmds:
        out = m.handle(c)
    return out


class Formats(unittest.TestCase):
    def test_sa_is_the_capture(self):
        lines, ev = ask(model.Modem(scen("sa")), 'AT+QENG="servingcell"')
        self.assertEqual(lines, ['+QENG: "servingcell","NOCONN","NR5G-SA","FDD",310,260,187461035,721,870100,'
                                 '127490,71,2,-99,-13,4,0,-'])
        self.assertTrue(matches(lines[0], "qeng_sa"))
        self.assertEqual(model.EVIDENCE[ev][0], "capture")

    def test_lte(self):
        m = model.Modem(scen("lte"))
        q, _ = m.handle('AT+QENG="servingcell"')
        self.assertTrue(matches(q[0], "qeng_lte"), q)
        ca, _ = m.handle("AT+QCAINFO")
        self.assertTrue(matches(ca[0], "qcainfo_lte_pcc"), ca)
        self.assertTrue(matches(ca[1], "qcainfo_lte_scc"), ca)
        self.assertEqual(ca[1].split(",")[2], "50", "QENG bw code 3 = 10 MHz = 50 RB")

    def test_nsa(self):
        m = model.Modem(scen("nsa"))
        q, ev = m.handle('AT+QENG="servingcell"')
        self.assertEqual(len(q), 3)
        self.assertTrue(matches(q[0], "qeng_head"), q[0])
        self.assertTrue(matches(q[1], "qeng_nsa_lte"), q[1])
        self.assertTrue(matches(q[2], "qeng_nsa_nr"), q[2])
        self.assertEqual(ev, "qeng-nsa")
        ca, _ = m.handle("AT+QCAINFO")
        self.assertTrue(matches(ca[0], "qcainfo_lte_pcc"), ca)
        self.assertTrue(matches(ca[1], "qcainfo_nr_scc"), ca)

    def test_qcainfo_extend_off_drops_nr_measurements(self):
        m = model.Modem(scen("nsa"))
        self.assertEqual(m.handle("AT+QCAINFO?")[0], ["+QCAINFO: 1"])
        self.assertEqual(m.handle("AT+QCAINFO=0")[0], [])
        ca, _ = m.handle("AT+QCAINFO")
        self.assertTrue(matches(ca[1], "qcainfo_nr_scc_short"), ca)
        m2 = model.Modem(dict(scen("sa"), qcainfo_extend=0))
        ca, _ = m2.handle("AT+QCAINFO")
        self.assertTrue(matches(ca[0], "qcainfo_nr_short"), ca)
        self.assertIsNone(m2.handle("AT+QCAINFO=2")[0])

    def test_sa_qcainfo(self):
        ca, _ = model.Modem(scen("sa")).handle("AT+QCAINFO")
        self.assertEqual(ca, ['+QCAINFO: "PCC",127490,2,"NR5G BAND 71",721,-99,-13,4'])
        self.assertTrue(matches(ca[0], "qcainfo_nr"))

    def test_neighbours(self):
        m = model.Modem(scen("nsa"))
        n, ev = m.handle('AT+QENG="neighbourcell"')
        self.assertEqual(model.EVIDENCE[ev][0], "baseband")
        self.assertTrue(matches(n[0], "ncell_intra"), n[0])
        self.assertTrue(matches(n[1], "ncell_inter"), n[1])
        self.assertEqual(model.Modem(scen("sa")).handle('AT+QENG="neighbourcell"')[0], [], "SA: a bare OK")

    def test_ambr_per_rat(self):
        sa, lte = model.Modem(scen("sa")), model.Modem(scen("lte"))
        nr, _ = sa.handle('AT+QNWCFG="nr5g_ambr"')
        self.assertTrue(matches(nr[0], "nr5g_ambr"), nr)
        self.assertEqual(sa.handle('AT+QNWCFG="lte_ambr"')[0], [])
        l, _ = lte.handle('AT+QNWCFG="lte_ambr"')
        self.assertTrue(matches(l[0], "lte_ambr"), l)
        self.assertIn('"broadband"', l[0], "the SIM's APN: GL matches the AMBR line to it")

    def test_qsimtype(self):
        m = model.Modem(scen("dual-sim"))
        r, ev = m.handle('AT+QSIMTYPE="slot2"')
        self.assertTrue(matches(r[0], "qsimtype"), r)
        self.assertEqual(model.EVIDENCE[ev][0], "atcop")
        m.handle('AT+QSIMTYPE="slot2",1')
        self.assertEqual(m.handle('AT+QSIMTYPE="slot2"')[0], ['+QSIMTYPE: "slot2",1'])
        esim = model.Modem({"subs": {"2": {"sim": {"present": True, "esim": True}}}})
        self.assertEqual(esim.handle('AT+QSIMTYPE="slot2"')[0], ['+QSIMTYPE: "slot2",1'])

    def test_counters(self):
        m = model.Modem(scen("sa"))
        a, _ = m.handle("AT+QGDCNT?")
        b, _ = m.handle("AT+QGDCNT?")
        self.assertTrue(matches(a[0], "qgdcnt"))
        self.assertGreater(int(b[0].split(",")[1]), int(a[0].split(",")[1]), "counting while camped")


class State(unittest.TestCase):
    def test_per_subscription(self):
        m = model.Modem(scen("dual-sim"))
        self.assertEqual(ask(m, "AT+QUIMSUB=1", "AT+CIMI")[0], ["310260000000001"])
        self.assertEqual(ask(m, "AT+QUIMSUB=2", "AT+CIMI")[0], ["310410000000002"])
        ask(m, "AT+QUIMSUB=2", 'AT+QNWPREFCFG="lte_band",66')
        self.assertEqual(ask(m, "AT+QUIMSUB=1", 'AT+QNWPREFCFG="lte_band"')[0][0].split(",")[1],
                         scen("dual-sim")["subs"]["1"].get("policy", model.DEFAULT_SUB["policy"])["lte_band"],
                         "a write for sub 2 leaves sub 1 alone")
        self.assertEqual(ask(m, "AT+QUIMSUB=2", 'AT+QNWPREFCFG="lte_band"')[0], ['+QNWPREFCFG: "lte_band",66'])

    def test_empty_slot(self):
        m = model.Modem(scen("sa"))
        self.assertIsNone(ask(m, "AT+QUIMSUB=2", "AT+CPIN?")[0])
        self.assertEqual(ask(m, 'AT+QENG="servingcell"')[0], ['+QENG: "servingcell","SEARCH"'])

    def test_prefs_steer_the_rat(self):
        def rat(m):
            q = m.handle('AT+QENG="servingcell"')[0][0]
            return "SEARCH" if "SEARCH" in q else ("NSA" if q == '+QENG: "servingcell","CONNECT"' else q.split(",")[2].strip('"'))
        m = model.Modem(scen("sa"))
        self.assertEqual(rat(m), "NR5G-SA")
        m.handle('AT+QNWPREFCFG="nr5g_disable_mode",1')
        self.assertEqual(rat(m), "LTE", "SA off -> the scenario's fallback")
        m.handle('AT+QNWPREFCFG="nr5g_disable_mode",0')
        m.handle('AT+QNWPREFCFG="nr5g_band",41:77')
        self.assertEqual(rat(m), "LTE", "n71 filtered out")
        m.handle('AT+QNWPREFCFG="nr5g_band",71')
        self.assertEqual(rat(m), "NR5G-SA")
        m.handle('AT+QNWPREFCFG="mode_pref",LTE')
        self.assertEqual(rat(m), "LTE")
        self.assertIsNone(m.handle('AT+QNWPREFCFG="nr5g_disable_mode",3')[0])
        n = model.Modem(scen("nsa"))
        self.assertEqual(rat(n), "NSA")
        n.handle('AT+QNWPREFCFG="nr5g_disable_mode",2')
        self.assertEqual(rat(n), "LTE", "NSA off -> the anchor alone")

    def test_cfun(self):
        m = model.Modem(scen("sa"))
        m.handle("AT+CFUN=4")
        self.assertIn("SEARCH", m.handle('AT+QENG="servingcell"')[0][0])
        self.assertEqual(m.handle("AT+COPS?")[0], ["+COPS: 0"])
        m.handle("AT+CFUN=1")
        self.assertIn("NR5G-SA", m.handle('AT+QENG="servingcell"')[0][0])

    def test_cgatt(self):
        m = model.Modem(scen("sa"))
        self.assertEqual(m.handle("AT+CGATT?")[0], ["+CGATT: 1"])
        m.handle("AT+CGATT=0")
        self.assertEqual(m.handle("AT+CGATT?")[0], ["+CGATT: 0"])
        self.assertIn("SEARCH", m.handle('AT+QENG="servingcell"')[0][0])
        m.handle("AT+CGATT=1")
        self.assertEqual(m.handle("AT+CGATT?")[0], ["+CGATT: 1"])

    def test_pdp(self):
        m = model.Modem(scen("sa"))
        self.assertEqual(m.handle("AT+CGACT?")[0], ["+CGACT: 1,1"])
        m.handle("AT+CGACT=0,1")
        self.assertEqual(m.handle("AT+CGACT?")[0], ["+CGACT: 1,0"])
        self.assertEqual(m.handle("AT+CGPADDR=1")[0], ['+CGPADDR: 1,"0.0.0.0"'])
        m.handle("AT+CGACT=1,1")
        self.assertEqual(m.handle("AT+CGPADDR=1")[0], ['+CGPADDR: 1,"10.0.0.2"'])

    def test_qnwlock(self):
        m = model.Modem(scen("sa"))
        self.assertEqual(m.handle('AT+QNWLOCK="common/5g"')[0], ['+QNWLOCK: "common/5g",0'])
        m.handle('AT+QNWLOCK="common/5g",721,127490,15,71')
        self.assertEqual(m.handle('AT+QNWLOCK="common/5g"')[0], ['+QNWLOCK: "common/5g",721,127490,15,71'])
        self.assertIsNone(m.handle('AT+QNWLOCK="bogus"')[0])

    def test_live_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            live = os.path.join(d, "live.json")
            m = model.Modem(scen("no-service"), live=live)
            self.assertIn("SEARCH", m.handle('AT+QENG="servingcell"')[0][0])
            with open(live, "w") as f:
                json.dump({"subs": {"1": {"serving": dict(scen("sa")["subs"]["1"]["serving"])}}}, f)
            self.assertIn("NR5G-SA", m.handle('AT+QENG="servingcell"')[0][0])
            os.utime(live, (1, 1))
            with open(live, "w") as f:
                json.dump({"replies": {"AT+CSQ": "+CSQ: 31,99"}}, f)
            self.assertEqual(m.handle("AT+CSQ"), (["+CSQ: 31,99"], "scenario"))

    def test_strict_and_unmodelled(self):
        self.assertEqual(model.Modem().handle("AT+QFOO"), ([], "unmodelled"))
        self.assertEqual(model.Modem(strict=True).handle("AT+QFOO"), (None, "unmodelled"))


class Catalogue(unittest.TestCase):
    def test_every_scenario_loads_and_answers(self):
        for f in sorted(os.listdir(SCEN)):
            with self.subTest(f):
                m = model.Modem(scen(f[:-5]))
                for c in ("AT+CPIN?", 'AT+QENG="servingcell"', "AT+QCAINFO", "AT+COPS?", 'AT+QNWPREFCFG="policy_band"'):
                    lines, ev = m.handle(c)
                    self.assertIn(ev, model.EVIDENCE)

    def test_evidence_sources_are_known(self):
        known = {"capture", "baseband", "atcop", "gl-parser", "doc", "gl-run", "assumed", "unmodelled", "scenario"}
        for k, (src, ref) in model.EVIDENCE.items():
            self.assertIn(src, known, k)
            self.assertTrue(ref, k)


if __name__ == "__main__":
    unittest.main()
