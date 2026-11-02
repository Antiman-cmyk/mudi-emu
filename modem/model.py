#!/usr/bin/env python3
"""The RG650V behaviour model: what GL's stack (or anything else) sees on the modem's AT port.

    model.py --link <path> --log <at.jsonl> [--scenario F] [--live F] [--strict]   serve on a pty
    model.py --evidence                                    every behaviour and the evidence for it
    model.py --repl [--scenario F]                         type AT commands by hand

Not an emulation of the baseband (a Hexagon/QuRT image tied to Qualcomm hardware and a live radio
network — see docs/limits.md): a STATE MACHINE that answers like the modem does, as far as we
have evidence, and says so. Every reply carries an evidence id (EVIDENCE below; also logged per
command in at.jsonl), one of:
  capture   seen on a real RG650V (reference/quectel-at-reference.md in MudiModem)
  baseband  the reply's format string in the baseband image (NON-HLOS modem.b24)
  atcop     the reply's format string in Quectel's apps-side AT processor (rootfs libql_atcop.so)
  gl-parser the format GL's own parser applies (rootfs libcm_modem.so sscanf / field indexes)
  doc       Quectel / 3GPP 27.007 documentation
  gl-run    GL's own code consumed it correctly in `mudi-emu up` (its parser agrees)
  assumed   plausible, unconfirmed — do not rely on it for a conclusion
  unmodelled  not implemented: answered `OK` so callers carry on (`ERROR` with --strict)
State is per subscription (AT+QUIMSUB selects which one later commands address, as GL's AT
service does before every command) and is shaped by the scenario (modem/scenarios/*.json,
modem/README.md); --live names a JSON file merged over the scenario on every command, so a
test can change the radio picture while the stack runs (`mudi-emu modem '{...}'`)."""
import copy, json, os, re, select, sys, time, tty

EVIDENCE = {
    "basic": ("doc", "3GPP 27.007: AT, ATE, AT+CMEE answer OK"),
    "ident": ("capture", "RG650VNA01ACR02A04G8G revision (reference §1); model/IMEI strings assumed"),
    "cpin": ("doc", "27.007 +CPIN: READY; no SIM -> a plain ERROR here (the module would say +CME ERROR: 10 "
                    "with AT+CMEE on; GL, which never sets CMEE, treats either as failure -- assumed)"),
    "quimsub": ("gl-run", "GL's AT service sends AT+QUIMSUB=<sub> before each command (modem_AT @0x7a68); "
                          "the model addresses per-subscription state with it"),
    "quimslot": ("doc", "Quectel AT+QUIMSLOT=<slot> / ? (+QUIMSLOT: <slot>); GL libcm_modem templates"),
    "sim-ids": ("doc", "27.007 AT+CIMI / Quectel +ICCID:, +QCCID:; values from the scenario"),
    "crsm-ad": ("assumed", "27.007 +CRSM: 144,0,<EF_AD> for the MNC-length read GL makes; value assumed"),
    "cops": ("doc", "27.007 +COPS: 0,0,\"<oper>\",<AcT> (7 E-UTRAN, 12 NG-RAN, 13 E-UTRA-NR dual)"),
    "creg": ("doc", "27.007 +CREG/+CEREG/+C5GREG: 0,<stat> (1 home, 2 searching)"),
    "csq": ("capture", "+CSQ: 16,99 (reference §4.5)"),
    "qspn": ("capture", "+QSPN: \"T-Mobile\",\"T-Mobile\",\"\",0,\"310260\" (reference §4.4)"),
    "qeng-sa": ("capture", "SA servingcell line, field for field (reference §4.1); baseband modem.b24 quectel_eng_atc.c"),
    "qeng-lte": ("baseband", "modem.b24 servingcell pieces: \"servingcell\",<state>,\"LTE\",<duplex>, mcc,mnc, "
                             "%X(cell),%d(pci),%ld(earfcn),%d(band),%d,%d(ul/dl bw),%X(tac),rsrp,rsrq,rssi,sinr,%d(cqi),tx_power,srxlev"),
    "qeng-nsa": ("baseband", "modem.b24: +QENG: \"servingcell\",\"%s\" / +QENG: \"LTE\",<duplex>,<LTE body> / "
                             "+QENG: \"NR5G-NSA\",%s(mcc),%s(mnc),%d(pci),%s(rsrp),%s(sinr),%s(rsrq),%ld(arfcn),%d(band),%d(bw) "
                             "-- no SCS field; gl-run: GL turns it into rows 4 + 51"),
    "qeng-search": ("baseband", "+QENG: \"servingcell\",\"%s\" with SEARCH when not camped (state name: doc)"),
    "qeng-ncell": ("baseband", "only LTE + WCDMA neighbour queries exist (quec_qeng_query_lte_ncell); LTE intra "
                               "\"%s\",\"%s\",%ld,%d,%d,%d,%d,%s, + 5 ints; SA answers a bare OK (capture)"),
    "qcainfo": ("baseband", "modem.b24: LTE PCC \"PCC\",%lu,%d(RBs),\"LTE BAND %lu\",state,pci,rsrp,rsrq,rssi,sinr (10 fields; "
                            "capture reference §4.6); LTE SCC the same + %d(ul_configured),%s,%s (13); NR \"%s\",%lu,%d(bw code),"
                            "\"NR5G BAND %lu\",pci[,rsrp,rsrq,sinr] (5 or 8); NR SCC \"SCC\",...,state,pci,%d(ul_configured),%s,%s"
                            "[,rsrp,rsrq,sinr] (9 or 12). gl-parser agrees: >= 9 fields -> pci@5, rsrp@9, rsrq@10"),
    "qcainfo-ext": ("assumed", "the short (5/9) vs measured (8/12) NR forms follow AT+QCAINFO=<0|1> (modem.b24 NV "
                               "quectel_cainfo_extend_enable_cfg; GL sets 1 when it reads 0); UL tail values (0,-,-) assumed"),
    "qcainfo-mode": ("baseband", "+QCAINFO: %ld (query) / +QCAINFO: (0,1) (test)"),
    "ambr": ("baseband", "modem.b24 +QNWCFG: \"lte_ambr\",\"%s\",%d,%d (APN, DL, UL kbps) / +QNWCFG: \"nr5g_ambr\",\"%s\","
                         "%d,%d,%d,%d (DNN, unit, DL, unit, UL; unit n = 4^(n-1) kbps, 3GPP 24.501); gl-parser: GL /1000 -> Mbps. "
                         "Which RAT answers which key (LTE/NSA lte_ambr, SA nr5g_ambr) is assumed"),
    "qsimtype": ("atcop", "libql_atcop.so: +QSIMTYPE: \"slot2\",%d, test (0,1); gl-parser rg650v_slot_is_esim: 1 = eSIM "
                          "(slot 1 is never asked); a real box answered 1 (reference crossed-response note)"),
    "qcsq": ("doc", "Quectel +QCSQ: \"<sysmode>\",..."),
    "qnwinfo": ("capture", "+QNWINFO: \"FDD NR5G\",\"310260\",\"NR5G BAND 71\",127490 (reference §4.3)"),
    "qnwprefcfg": ("baseband", "quectel_nwpref_atc.c: keys gw_band lte_band nsa_nr5g_band nr5g_band mode_pref "
                               "srv_domain voice_domain roam_pref ue_usage_setting policy_band ue_capability_band "
                               "rat_acq_order nr5g_disable_mode(0-2) rf_band restore_band policy_mode; "
                               "query reply +QNWPREFCFG: \"<key>\",<value>"),
    "qnwprefcfg-bands": ("capture", "policy_band / ue_capability_band answer one +QNWPREFCFG line per band key"),
    "qnwlock": ("capture", "query/set forms of common/4g, common/5g, save_ctrl (reference QNWLOCK section)"),
    "cgatt": ("doc", "27.007 +CGATT: <0|1> (PS attach); gl-parser: GL reads the int after \"+CGATT: \" "
                     "(libcm_modem.so), polling it while it redials; AT+CGATT=0 detaches until =1"),
    "cfun": ("doc", "27.007 AT+CFUN=<0|1|4>; 4 = radio off (the model drops registration until CFUN=1)"),
    "qgdcnt": ("baseband", "+QGDCNT: %llu,%llu / +QGDNRCNT: %llu,%llu (sent, received bytes)"),
    "pdp": ("doc", "27.007 +CGDCONT / +CGACT / +CGPADDR forms; APN and address from the scenario. Context 1 "
                   "starts active while camped; AT+CGACT=<0|1>,1 changes it (GL deactivates it when it redials "
                   "after a band write; its re-activation goes over QMI, which does not exist here)"),
    "misc-ok": ("assumed", "commands GL sends whose reply carries nothing the model tracks (other QNWCFG/QCFG keys, ...)"),
    "rat-select": ("assumed", "serving RAT follows the prefs: SA needs NR5G in mode_pref and nr5g_disable_mode != 1 "
                              "and its band allowed; else the scenario's fallback (NSA if it has an anchor, else LTE)"),
    "scenario": ("scenario", "a reply the scenario overrides verbatim (\"replies\")"),
    "unmodelled": ("unmodelled", "not implemented: OK (ERROR with --strict); logged so it shows up"),
}

LTE_RB = {0: 6, 1: 15, 2: 25, 3: 50, 4: 75, 5: 100}      # QENG LTE bandwidth code -> QCAINFO resource blocks

DEFAULT_SUB = {
    "sim": {"present": True, "imsi": "310260000000001", "iccid": "8901260000000000001", "mcc": "310", "mnc": "260",
            "operator": "T-Mobile", "spn": "T-Mobile", "apn": "fast.t-mobile.com", "ip": "10.0.0.2", "esim": False,
            "ambr": {"dl_kbps": 600000, "ul_kbps": 100000}},
    "serving": {"rat": "SA", "state": "NOCONN", "duplex": "FDD", "cell_id": "187461035", "tac": "870100",
                "pci": 721, "arfcn": 127490, "band": 71, "bw": 2, "rsrp": -99, "rsrq": -13, "sinr": 4, "rssi": -65},
    "anchor": None,                 # NSA: the LTE anchor {cell_id, tac, pci, earfcn, band, bw, rsrp, rsrq, rssi, sinr}
    "scc": [],                      # extra carriers {rat: "LTE"|"NR", freq, bw, band, pci, rsrp, rsrq, sinr}
    "fallback": None,               # serving picture when prefs exclude the scenario's RAT (else: no service)
    "neighbours": [],               # LTE {kind: intra|inter, earfcn, pci, rsrq, rsrp, rssi, sinr, srxlev}
    "policy": {"lte_band": "2:4:5:7:12:13:14:17:25:26:29:30:38:41:42:43:48:66:71",
               "nsa_nr5g_band": "2:5:7:12:13:14:25:26:29:30:38:41:48:66:70:71:77:78",
               "nr5g_band": "2:5:7:12:13:14:25:26:29:30:38:41:48:66:70:71:77:78"},
    "traffic": {"tx": 1000000, "rx": 50000000, "tx_step": 12345, "rx_step": 543210},
}
DEFAULT = {"slot": 1, "qcainfo_extend": 1, "subs": {"1": DEFAULT_SUB, "2": {"sim": {"present": False}}}, "replies": {}}


def merge(a, b):
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


class Modem:
    def __init__(self, scenario=None, live=None, strict=False):
        self.base = merge(DEFAULT, scenario or {})
        for s, sub in list(self.base["subs"].items()):
            self.base["subs"][s] = merge(DEFAULT_SUB, sub) if sub.get("sim", {}).get("present", True) else merge({"sim": {"present": False}}, sub)
        self.live_path, self.live_mtime, self.live = live, None, {}
        self.strict = strict
        self.sub = 1
        self.slot = self.base.get("slot", 1)
        self.cfun = 1
        self.attach = {}                # per sub: False after AT+CGATT=0
        self.pdp = {}                   # per sub: context 1 active? (default: while camped)
        self.qcainfo_ext = int(self.base.get("qcainfo_extend", 1))
        self.simtype2 = 1 if (self.base["subs"].get("2") or {}).get("sim", {}).get("esim") else 0
        prefs0 = {"mode_pref": "AUTO", "nr5g_disable_mode": "0", "roam_pref": "255", "srv_domain": "2",
                  "voice_domain": "3", "ue_usage_setting": "1", "rat_acq_order": "NR5G:LTE",
                  "lte_band": None, "nsa_nr5g_band": None, "nr5g_band": None}
        self.prefs = {s: dict(prefs0) for s in self.base["subs"]}
        self.locks = {s: {"common/4g": "0", "common/5g": "0", "save_ctrl": "0,0", "common/4g_ext": "0"} for s in self.base["subs"]}
        self.counters = {s: dict(self.base["subs"][s].get("traffic") or {}) for s in self.base["subs"]}

    # ---- the scenario as of now (live overrides merged in) --------------------------------------
    def cfg(self):
        if self.live_path:
            try:
                m = os.stat(self.live_path).st_mtime
                if m != self.live_mtime:
                    self.live_mtime = m
                    with open(self.live_path) as f:
                        self.live = json.load(f)
            except (OSError, ValueError):
                pass
        return merge(self.base, self.live)

    def subcfg(self, c=None):
        c = c or self.cfg()
        return c["subs"].get(str(self.sub)) or {"sim": {"present": False}}

    def pref(self, key, sub=None):
        sub = str(sub or self.sub)
        v = self.prefs.setdefault(sub, {}).get(key)
        if v is None and key in ("lte_band", "nsa_nr5g_band", "nr5g_band"):
            v = (self.subcfg().get("policy") or {}).get(key, "")
        return v

    # ---- which radio the subscription is on, given its prefs (EVIDENCE rat-select: assumed) -----
    def serving(self, s):
        if self.cfun != 1 or not self.attach.get(str(self.sub), True) or not s.get("sim", {}).get("present", True):
            return None
        srv = dict(s.get("serving") or {})
        rat = srv.get("rat", "NONE")
        mode = (self.pref("mode_pref") or "AUTO").upper()
        dis = str(self.pref("nr5g_disable_mode") or "0")

        def allowed(key, band):
            lst = str(self.pref(key) or "")
            return band is None or not lst or str(band) in lst.split(":")
        nr_ok = mode == "AUTO" or "NR5G" in mode
        lte_ok = mode == "AUTO" or "LTE" in mode
        ok = {"SA": nr_ok and dis != "1" and allowed("nr5g_band", srv.get("band")),
              "NSA": nr_ok and lte_ok and dis != "2" and allowed("nsa_nr5g_band", srv.get("band"))
                     and allowed("lte_band", (s.get("anchor") or {}).get("band")),
              "LTE": lte_ok and allowed("lte_band", srv.get("band")),
              "NONE": True}.get(rat, False)
        if ok:
            return srv if rat != "NONE" else None
        fb = s.get("fallback")
        if fb and fb.get("rat") != rat:
            return self.serving(dict(s, serving=fb, fallback=None))
        return None

    # ---- replies ---------------------------------------------------------------------------------
    def handle(self, cmd):
        """-> (lines | None for ERROR, evidence id)"""
        c = self.cfg()
        if cmd in c.get("replies", {}):
            r = c["replies"][cmd]
            return (None if r is None else [x for x in r.split("\n") if x]), "scenario"
        u = cmd.upper()
        s = self.subcfg(c)
        sim = s.get("sim", {})
        present = sim.get("present", True)
        srv = self.serving(s)

        if u in ("AT", "ATE0", "ATE1", "ATV1", "ATQ0") or re.match(r"AT\+CMEE=\d", u):
            return [], "basic"
        m = re.match(r"AT\+QUIMSUB=(\d)$", u)
        if m:
            self.sub = int(m.group(1)) if m.group(1) in ("1", "2") else 1
            return [], "quimsub"
        if u == "AT+QUIMSUB?":
            return ["+QUIMSUB: %d" % self.sub], "quimsub"
        m = re.match(r"AT\+QUIMSLOT=(\d)$", u)
        if m:
            self.slot = int(m.group(1))
            return [], "quimslot"
        if u == "AT+QUIMSLOT?":
            return ["+QUIMSLOT: %d" % self.slot], "quimslot"
        if u in ("AT+CGMR", "AT+QGMR"):
            return ["RG650VNA01ACR02A04G8G"], "ident"
        if u in ("AT+CGMM", "AT+GMM"):
            return ["RG650V-NA"], "ident"
        if u in ("AT+CGMI", "AT+GMI"):
            return ["Quectel"], "ident"
        if u in ("AT+GSN", "AT+CGSN"):
            return ["860000000000001"], "ident"
        if u == "ATI":
            return ["Quectel", "RG650V-NA", "Revision: RG650VNA01ACR02A04G8G"], "ident"
        if u == "AT+CPIN?":
            return (["+CPIN: READY"], "cpin") if present else (None, "cpin")
        if u == "AT+CIMI":
            return ([sim.get("imsi", "")], "sim-ids") if present else (None, "sim-ids")
        if u in ("AT+ICCID", "AT+QCCID"):
            return (["+%s: %s" % (u[3:], sim.get("iccid", ""))], "sim-ids") if present else (None, "sim-ids")
        if u.startswith("AT+CRSM=176,28589"):
            return (['+CRSM: 144,0,"0000000%d"' % len(sim.get("mnc", "26"))], "crsm-ad") if present else (None, "crsm-ad")
        if u == "AT+CNUM":
            return [], "sim-ids"
        if u == "AT+COPS?":
            if not srv:
                return ["+COPS: 0"], "cops"
            act = {"SA": 12, "NSA": 13, "LTE": 7}.get(srv.get("rat"), 7)
            return ['+COPS: 0,0,"%s",%d' % (sim.get("operator", ""), act)], "cops"
        m = re.match(r"AT\+(CREG|CEREG|C5GREG|CGREG)\?$", u)
        if m:
            reg = srv and (m.group(1) != "C5GREG" or srv.get("rat") == "SA")
            return ["+%s: 0,%d" % (m.group(1), 1 if reg else 2)], "creg"
        if u == "AT+CSQ":
            return ["+CSQ: %s" % ("16,99" if srv else "99,99")], "csq"
        if u == "AT+QSPN":
            return (['+QSPN: "%s","%s","",0,"%s%s"' % (sim.get("operator", ""), sim.get("spn", ""), sim.get("mcc", ""),
                                                        sim.get("mnc", ""))], "qspn") if present else (None, "qspn")
        if u == 'AT+QENG="SERVINGCELL"':
            return self.qeng(sim, srv, s)
        if u == 'AT+QENG="NEIGHBOURCELL"':
            if not srv or srv.get("rat") == "SA":
                return [], "qeng-ncell"
            out = []
            for n in s.get("neighbours") or []:
                def v(k):
                    return "-" if n.get(k) is None else str(n[k])
                if n.get("kind", "intra") == "intra":
                    out.append('+QENG: "neighbourcell intra","LTE",%s,%s,%s,%s,%s,%s,%s,-,-,-,-' % (
                        n["earfcn"], v("pci"), v("rsrq"), v("rsrp"), v("rssi"), v("sinr"), v("srxlev")))
                else:
                    out.append('+QENG: "neighbourcell inter","LTE",%s,%s,%s,%s,%s,%s,%s,-,-,-' % (
                        n["earfcn"], v("pci"), v("rsrq"), v("rsrp"), v("rssi"), v("sinr"), v("srxlev")))
            return out, "qeng-ncell"
        if u == "AT+QCAINFO":
            return self.qcainfo(srv, s)
        if u == "AT+QCAINFO?":
            return ["+QCAINFO: %d" % self.qcainfo_ext], "qcainfo-mode"
        if u == "AT+QCAINFO=?":
            return ["+QCAINFO: (0,1)"], "qcainfo-mode"
        m = re.match(r"AT\+QCAINFO=(\d+)$", u)
        if m:
            if m.group(1) not in ("0", "1"):
                return None, "qcainfo-mode"
            self.qcainfo_ext = int(m.group(1))
            return [], "qcainfo-mode"
        if u == "AT+QCSQ":
            if not srv:
                return ['+QCSQ: "NOSERVICE"'], "qcsq"
            mode = {"SA": "NR5G", "NSA": "NR5G-NSA", "LTE": "LTE"}[srv["rat"]]
            return ['+QCSQ: "%s",%s,%s,%s' % (mode, srv.get("rsrp"), srv.get("sinr"), srv.get("rsrq"))], "qcsq"
        if u == "AT+QNWINFO":
            if not srv:
                return ["+QNWINFO: No Service"], "qnwinfo"
            nr = srv["rat"] == "SA"
            return ['+QNWINFO: "%s %s","%s%s","%s BAND %s",%s' % (srv.get("duplex", "FDD"), "NR5G" if nr else "LTE",
                    sim.get("mcc"), sim.get("mnc"), "NR5G" if nr else "LTE", srv.get("band"), srv.get("arfcn"))], "qnwinfo"
        m = re.match(r'AT\+QNWPREFCFG="([a-z0-9_]+)"(?:,(.*))?$', cmd, re.I)
        if m:
            return self.qnwprefcfg(m.group(1).lower(), m.group(2), s)
        m = re.match(r'AT\+QNWLOCK="([a-z0-9/_]+)"(?:,(.*))?$', cmd, re.I)
        if m:
            k, v = m.group(1).lower(), m.group(2)
            L = self.locks.setdefault(str(self.sub), {})
            if k not in ("common/4g", "common/5g", "save_ctrl", "common/4g_ext"):
                return None, "qnwlock"
            if v is None:
                return ['+QNWLOCK: "%s",%s' % (k, L.get(k, "0"))], "qnwlock"
            L[k] = v
            return [], "qnwlock"
        m = re.match(r"AT\+CFUN=(\d)", u)
        if m:
            self.cfun = int(m.group(1))
            return [], "cfun"
        if u == "AT+CGATT?":
            return ["+CGATT: %d" % (1 if srv else 0)], "cgatt"
        m = re.match(r"AT\+CGATT=([01])$", u)
        if m:
            self.attach[str(self.sub)] = m.group(1) == "1"
            return [], "cgatt"
        if u == "AT+CFUN?":
            return ["+CFUN: %d" % self.cfun], "cfun"
        if u in ("AT+QGDCNT?", "AT+QGDNRCNT?"):
            t = self.counters.setdefault(str(self.sub), {})
            if srv:
                t["tx"] = t.get("tx", 0) + t.get("tx_step", 0)
                t["rx"] = t.get("rx", 0) + t.get("rx_step", 0)
            tag = "QGDCNT" if u == "AT+QGDCNT?" else "QGDNRCNT"
            div = 1 if tag == "QGDCNT" else 2
            return ["+%s: %d,%d" % (tag, t.get("tx", 0) // div, t.get("rx", 0) // div)], "qgdcnt"
        if u == "AT+CGDCONT?":
            return (['+CGDCONT: 1,"IPV4V6","%s","0.0.0.0",0,0' % sim.get("apn", "")] if present else []), "pdp"
        act = bool(srv) and self.pdp.get(str(self.sub), True)
        if u == "AT+CGACT?":
            return (["+CGACT: 1,%d" % (1 if act else 0)] if present else []), "pdp"
        m = re.match(r"AT\+CGACT=([01]),1$", u)
        if m:
            if not present:
                return None, "pdp"
            self.pdp[str(self.sub)] = m.group(1) == "1"
            return [], "pdp"
        if u.startswith("AT+CGPADDR"):
            return (['+CGPADDR: 1,"%s"' % (sim.get("ip") if act else "0.0.0.0")] if present else []), "pdp"
        m = re.match(r'AT\+QNWCFG="(LTE|NR5G)_AMBR"$', u)
        if m:
            amb = sim.get("ambr") or {}
            want = "LTE" if m.group(1) == "LTE" else "SA"
            if not srv or not amb or (srv["rat"] == "SA") != (want == "SA"):
                return [], "ambr"
            apn = amb.get("apn") or sim.get("apn", "")
            dl, ul = int(amb.get("dl_kbps", 0)), int(amb.get("ul_kbps", 0))
            if want == "LTE":
                return ['+QNWCFG: "lte_ambr","%s",%d,%d' % (apn, dl, ul)], "ambr"
            return ['+QNWCFG: "nr5g_ambr","%s",6,%d,6,%d' % (apn, round(dl / 1024), round(ul / 1024))], "ambr"
        if u == 'AT+QSIMTYPE="SLOT2"':
            return ['+QSIMTYPE: "slot2",%d' % self.simtype2], "qsimtype"
        if u == 'AT+QSIMTYPE="SLOT2",?' or u == "AT+QSIMTYPE=?":
            return ['+QSIMTYPE: "slot2",(0,1)'], "qsimtype"
        m = re.match(r'AT\+QSIMTYPE="SLOT2",(\d+)$', u)
        if m:
            if m.group(1) not in ("0", "1"):
                return None, "qsimtype"
            self.simtype2 = int(m.group(1))
            return [], "qsimtype"
        if re.match(r'AT\+(QNWCFG|QCFG|QSIMDET|QSIMSTAT|QMBNCFG|QPINC|QEFSSYNC)', u):
            return [], "misc-ok"
        return (None if self.strict else []), "unmodelled"

    def qeng(self, sim, srv, s):
        if not srv:
            return ['+QENG: "servingcell","SEARCH"'], "qeng-search"
        st = srv.get("state", "NOCONN")
        if srv["rat"] == "SA":
            return ['+QENG: "servingcell","%s","NR5G-SA","%s",%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,-' % (
                st, srv.get("duplex", "FDD"), sim.get("mcc"), sim.get("mnc"), srv.get("cell_id"), srv.get("pci"),
                srv.get("tac"), srv.get("arfcn"), srv.get("band"), srv.get("bw"), srv.get("rsrp"), srv.get("rsrq"),
                srv.get("sinr"))], "qeng-sa"
        if srv["rat"] == "LTE":
            return ['+QENG: "servingcell","%s","LTE","%s",%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,15,0,-' % (
                st, srv.get("duplex", "FDD"), sim.get("mcc"), sim.get("mnc"), srv.get("cell_id"), srv.get("pci"),
                srv.get("arfcn"), srv.get("band"), srv.get("bw", 5), srv.get("bw", 5), srv.get("tac"), srv.get("rsrp"),
                srv.get("rsrq"), srv.get("rssi", -65), srv.get("sinr"))], "qeng-lte"
        a = s.get("anchor") or {}
        return ['+QENG: "servingcell","%s"' % st,
                '+QENG: "LTE","%s",%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,15,0,-' % (
                    a.get("duplex", "FDD"), sim.get("mcc"), sim.get("mnc"), a.get("cell_id"), a.get("pci"), a.get("earfcn"),
                    a.get("band"), a.get("bw", 5), a.get("bw", 5), a.get("tac"), a.get("rsrp"), a.get("rsrq"), a.get("rssi", -65),
                    a.get("sinr")),
                '+QENG: "NR5G-NSA",%s,%s,%s,%s,%s,%s,%s,%s,%s' % (
                    sim.get("mcc"), sim.get("mnc"), srv.get("pci"), srv.get("rsrp"), srv.get("sinr"), srv.get("rsrq"),
                    srv.get("arfcn"), srv.get("band"), srv.get("bw"))], "qeng-nsa"

    def qcainfo(self, srv, s):
        """Every carrier, PCC first (formats: EVIDENCE qcainfo). bw in the scenario is the QENG code
        (LTE 0-5, NR 0-14); LTE lines carry resource blocks, NR lines the code itself."""
        if not srv:
            return [], "qcainfo"
        ext = self.qcainfo_ext == 1
        ev = "qcainfo" if ext and not any(c.get("rat") == "NR" for c in s.get("scc") or []) and srv["rat"] == "LTE" else "qcainfo-ext"

        def lte(role, c):
            sinr5 = int(round((c.get("sinr", 0) + 20) * 5))           # 1/5 dB scale (reference §4.6)
            line = '+QCAINFO: "%s",%s,%s,"LTE BAND %s",%s,%s,%s,%s,%s,%s' % (
                role, c.get("earfcn", c.get("arfcn", c.get("freq"))), LTE_RB.get(c.get("bw", 5), 100), c.get("band"),
                c.get("ca_state", 1), c.get("pci"), c.get("rsrp"), c.get("rsrq"), c.get("rssi", -65), sinr5)
            return line + (",0,-,-" if role == "SCC" else "")

        def nr(role, c):
            head = '+QCAINFO: "%s",%s,%s,"NR5G BAND %s"' % (role, c.get("arfcn", c.get("freq")), c.get("bw"), c.get("band"))
            meas = ",%s,%s,%s" % (c.get("rsrp"), c.get("rsrq"), c.get("sinr")) if ext else ""
            if role == "PCC":
                return head + ",%s" % c.get("pci") + meas
            return head + ",%s,%s,0,-,-" % (c.get("ca_state", 1), c.get("pci")) + meas
        out = []
        if srv["rat"] == "LTE":
            out.append(lte("PCC", srv))
        elif srv["rat"] == "NSA":
            out.append(lte("PCC", s.get("anchor") or {}))
            out.append(nr("SCC", srv))
        else:
            out.append(nr("PCC", srv))
        for c in s.get("scc") or []:
            out.append(nr("SCC", c) if c.get("rat") == "NR" else lte("SCC", c))
        return out, ev

    def qnwprefcfg(self, k, v, s):
        keys = ("gw_band", "lte_band", "nsa_nr5g_band", "nr5g_band", "mode_pref", "srv_domain", "voice_domain",
                "roam_pref", "ue_usage_setting", "policy_band", "ue_capability_band", "rat_acq_order",
                "nr5g_disable_mode", "rf_band", "restore_band", "policy_mode")
        if k not in keys:
            return None, "qnwprefcfg"
        sub = str(self.sub)
        if k in ("policy_band", "ue_capability_band"):
            if v is not None:
                return None, "qnwprefcfg"
            pol = s.get("policy") or {}
            cap = s.get("capability") or pol
            src = pol if k == "policy_band" else cap
            return ['+QNWPREFCFG: "%s",%s' % (b, src.get(b, "")) for b in ("lte_band", "nsa_nr5g_band", "nr5g_band")], "qnwprefcfg-bands"
        if v is None:
            return ['+QNWPREFCFG: "%s",%s' % (k, self.pref(k) if self.pref(k) is not None else "")], "qnwprefcfg"
        if k == "nr5g_disable_mode" and v not in ("0", "1", "2"):
            return None, "qnwprefcfg"
        self.prefs.setdefault(sub, {})[k] = v
        return [], "qnwprefcfg"


def evidence_table():
    w = max(len(k) for k in EVIDENCE)
    for k, (src, ref) in EVIDENCE.items():
        print("%-*s  %-10s  %s" % (w, k, src, ref))


def serve(link, log, scenario, live, strict):
    m = Modem(scenario, live, strict)
    master, slave = os.openpty()
    tty.setraw(slave)
    try:
        os.unlink(link)
    except OSError:
        pass
    os.symlink(os.ttyname(slave), link)
    print(os.ttyname(slave), flush=True)
    buf = b""
    out = open(log, "a")
    while True:
        r, _, _ = select.select([master], [], [], 1.0)
        if not r:
            continue
        try:
            buf += os.read(master, 4096)
        except OSError:
            time.sleep(0.05)
            continue
        while True:
            mm = re.search(rb"[\r\n]", buf)
            if not mm:
                break
            line, buf = buf[:mm.start()].decode("latin-1").strip(), buf[mm.end():]
            if not line:
                continue
            sub, slot = m.sub, m.slot
            lines, ev = m.handle(line)
            out.write(json.dumps({"t": time.time(), "cmd": line, "sub": sub, "slot": slot, "reply": lines,
                                  "evidence": ev, "source": EVIDENCE[ev][0]}) + "\n")
            out.flush()
            resp = "\r\nERROR\r\n" if lines is None else "".join("\r\n%s\r\n" % x for x in lines) + "\r\nOK\r\n"
            os.write(master, resp.encode())


def main(argv):
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    scen = None
    if opt("--scenario"):
        with open(opt("--scenario")) as f:
            scen = json.load(f)
    if "--evidence" in argv:
        evidence_table()
    elif "--repl" in argv:
        m = Modem(scen)
        for line in sys.stdin:
            lines, ev = m.handle(line.strip())
            print("".join(x + "\n" for x in lines) + "OK" if lines is not None else "ERROR", "   [%s: %s]" % (ev, EVIDENCE[ev][0]))
    elif opt("--link") and opt("--log"):
        serve(opt("--link"), opt("--log"), scen, opt("--live"), "--strict" in argv)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
