# The modem behaviour model

`model.py` stands in for the Quectel RG650V's AT port (`/dev/smd9` on the E5800) behind a pty.
`mudi-emu up` starts it and points the image's AT port at it, so GL's own `cellular_manager` and
the `modem_AT` it spawns talk to it unmodified.

It is **not** an emulation of the baseband (see `../docs/limits.md`). It is a state machine that
answers the way the modem does, as far as there is evidence for it, and says so for every reply.

```sh
python3 -I modem/model.py --evidence                     # every behaviour + its evidence
python3 -I modem/model.py --repl --scenario modem/scenarios/nsa.json
```

## Evidence

Every reply carries an evidence id. `mudi-emu evidence` lists them all, and each line of
`<workdir>/at.jsonl` records the id and source next to the command and reply. Sources, strongest
first:

| source | meaning |
|---|---|
| `capture` | seen on a real RG650V-NA (MudiModem `reference/quectel-at-reference.md`) |
| `baseband` | the reply's printf format is in the baseband image (`NON-HLOS…bin` → `image/modem.b24`) |
| `atcop` | the format is in Quectel's apps-side AT processor (`/usr/lib/libql_atcop.so` in the rootfs) |
| `gl-parser` | the format GL's own parser applies (`/usr/lib/libcm_modem.so`: sscanf strings, field indexes) |
| `gl-run` | GL's code consumed the reply correctly under `mudi-emu up` |
| `doc` | Quectel / 3GPP TS 27.007 documentation only |
| `assumed` | plausible, unconfirmed. Don't draw conclusions from it |
| `unmodelled` | not implemented: answered `OK` (`ERROR` with `--strict`) and logged |
| `scenario` | a verbatim reply from the scenario's `replies` |

A format can be pinned while the behaviour behind it is still assumed. For example, the AMBR
reply formats are in the baseband, but which RAT answers which key is assumed. The evidence
text says which part is which. `tests/test_model.py` checks every modelled reply against the
baseband/atcop format strings it names.

To find what a run relied on beyond the evidence:

```sh
jq -r 'select(.source=="assumed" or .source=="unmodelled") | .cmd' work/x/at.jsonl | sort | uniq -c
```

## State

- **Per subscription.** `AT+QUIMSUB=<1|2>` selects the subscription that later commands address.
  GL's AT service sends it before every command. Prefs (`AT+QNWPREFCFG`), locks
  (`AT+QNWLOCK`) and traffic counters are kept per subscription.
- **Serving RAT follows the prefs** (`rat-select`, assumed): SA needs NR5G allowed by
  `mode_pref`, `nr5g_disable_mode` ≠ 1 and its band in `nr5g_band`. NSA needs NR5G and LTE
  allowed, `nr5g_disable_mode` ≠ 2, its NR band in `nsa_nr5g_band` and the anchor's band in
  `lte_band`. LTE needs its band in `lte_band`. Otherwise the scenario's `fallback` picture
  applies, or no service if there is none.
- `AT+CFUN=4` drops service until `AT+CFUN=1`. `AT+QCAINFO=<0|1>` switches the NR CA lines
  between the short and the measured forms (default 1). `AT+QSIMTYPE="slot2",<0|1>` sets the
  eSIM type.

## Scenarios

A scenario is a JSON file merged over the defaults (`DEFAULT` / `DEFAULT_SUB` in `model.py`). Every
key is optional. Keys starting with `_` are comments.

```jsonc
{
  "slot": 1,                       // AT+QUIMSLOT? (the physical slot in use)
  "qcainfo_extend": 1,             // AT+QCAINFO? at start
  "hw_info": {"country_code": "US"},   // extra /proc/gl-hw-info files for `up` (not the model)
  "replies": {"AT+CSQ": "+CSQ: 31,99", "AT+QFOO": null},  // verbatim; null = ERROR
  "subs": {
    "1": {
      "sim": {"present": true, "imsi": "...", "iccid": "...", "mcc": "310", "mnc": "260",
              "operator": "T-Mobile", "spn": "T-Mobile", "apn": "fast.t-mobile.com", "ip": "10.0.0.2",
              "esim": false,                                  // slot 2 only: QSIMTYPE
              "ambr": {"dl_kbps": 600000, "ul_kbps": 100000}},
      "serving": {"rat": "SA|NSA|LTE|NONE", "state": "NOCONN|CONNECT|...", "duplex": "FDD",
                  "cell_id": "187461035", "tac": "870100",    // as printed: hex digits (%X)
                  "pci": 721, "arfcn": 127490, "band": 71, "bw": 2,
                  "rsrp": -99, "rsrq": -13, "sinr": 4, "rssi": -65},
      "anchor": {...},             // NSA: the LTE anchor (cell_id, tac, pci, earfcn, band, bw, rsrp, rsrq, rssi, sinr)
      "fallback": {...},           // the picture when the prefs exclude `serving` (a serving dict)
      "scc": [{"rat": "LTE|NR", "freq": 900, "band": 2, "bw": 3, "pci": 301, "rsrp": -110, ...}],
      "neighbours": [{"kind": "intra|inter", "earfcn": 67036, "pci": 211, "rsrq": -14, "rsrp": -108,
                      "rssi": -80, "sinr": 2, "srxlev": 20}],
      "policy": {"lte_band": "2:4:...", "nsa_nr5g_band": "...", "nr5g_band": "..."},   // policy_band + prefs at start
      "capability": {...},         // ue_capability_band (default: = policy)
      "traffic": {"tx": 1000000, "rx": 50000000, "tx_step": 12345, "rx_step": 543210}  // QGDCNT, per query
    },
    "2": {"sim": {"present": false}}
  }
}
```

`bw` is always the QENG bandwidth code: LTE 0–5 (1.4/3/5/10/15/20 MHz; QCAINFO lines carry the
resource blocks 6/15/25/50/75/100), NR 0–14 (Quectel's table; GL maps 2 → 15, 11 → 90 and 12 → 100 MHz).

| scenario | picture |
|---|---|
| `sa.json` | SIM 1 on T-Mobile NR5G-SA n71 (the captured cell), LTE B66 fallback; SIM 2 empty |
| `nsa.json` | SIM 1 EN-DC: LTE B66 anchor + n78 (100 MHz), two LTE neighbours |
| `lte.json` | SIM 1 AT&T LTE B66 + B2 CA, neighbours |
| `no-service.json` | SIM 1 present, not camped |
| `dual-sim.json` | SIM 1 SA n71 (active), SIM 2 AT&T LTE B66 |

## Live changes

`--live <file>` names a JSON object that is merged over the scenario on every command (the file is
re-read when its mtime changes). Inside `mudi-emu up`, `mudi-emu modem '<json>'` merges into it, so
a test can move the radio while GL runs:

```sh
mudi-emu modem '{"subs":{"1":{"serving":{"rat":"NONE"}}}}'          # lose service
mudi-emu modem '{"subs":{"1":{"serving":{"rsrp":-112,"pci":333}}}}'  # weaker, another cell
mudi-emu modem '{"replies":{"AT+QCAINFO":"+QCAINFO: \"PCC\",..."}}'  # a verbatim reply
```

GL samples every 10 s, so wait ~20 s before reading what it made of a change. `up` clears the live
file at the start of every run.
