# GL 4.10 cellular stack: what it does, measured by running it

Everything here was measured by running GL's own binaries from the E5800 4.10.0 image under
`mudi-emu up`: `cellular_manager`, the `modem_AT` it spawns, and the `glc` CGI behind `/rpc`,
against the modem model, with every AT command logged. A fact holds for GL's code. The modem's
side of it is only as good as the evidence for the reply GL was given (`at.jsonl` records it).
To re-check a fact, run the scenario and read `at.jsonl` plus what GL returns. Where a fact also
comes from disassembly, the function is named.

## Bring-up (what `up` has to provide)
- GL reads the hardware from `/proc/gl-hw-info/*` (its kernel module serves the device tree's
  `gl-hw` node there): `build-in-modem=cpu`, `modem-cpu=rg650v`, `model=e5800`, … `up` writes
  them from `firmware/<image>/hw-info.json`, which `extract` builds from `boot.img`'s DTB, plus
  `country_code` (factory data, not in the DTB).
- The AT port comes from `/lib/modem_data/modem_list.json` (`RG650V`: bus CPU, `port_at` `/dev/smd9`);
  GL spawns `modem_AT -B cpu -P /dev/smd9 -O-1` itself.
- `cellular_manager` waits up to 60 s for Quectel's MCM ready flags
  (`/tmp/mcm_{atcop_,,sim_}service_ready.flag`, `check_ql_service_ready`) before it registers any
  ubus object.
- Data calls, DDS and SIM events go over QMI (`QMI_FW` log lines, "Get DDS Subscription ID
  failed"). There is no QMI service here, so no data call comes up, `cellular.collect
  get_traffic` is `{}`, and dial-time behaviour is reached through `cellular.cm cm_start_dial`.

## AT traffic
- `modem_AT` sends `AT+QUIMSUB=<sub_id>` before each command. `sub_id` = slot (1, 2) for the
  built-in modem.
- GL samples **both SIMs every 10 s**: `QUIMSUB=1`, `AT+QENG="servingcell"`, `AT+QCAINFO`, then
  the same for sub 2. Other callers add more QENG traffic (about 4 per 10 s in total).
- At start it also sends `AT+ICCID`, `AT+CIMI`, `AT+CRSM=176,28589,0,0,4` (EF_AD, MNC length),
  `AT+CPIN?`, `AT+COPS?`, `AT+CEREG?`/`AT+C5GREG?`, `AT+CNUM`, `AT+CGDCONT?`, `AT+QSIMTYPE="slot2"`,
  `AT+QNWCFG="lte_ambr"`/`"nr5g_ambr"`, `AT+CGMM`, `AT+CGSN`, `AT+QGMR` and `AT+QCAINFO?`. If that
  reads 0, it sends `AT+QCAINFO=1` (`ensure_quectel_qcainfo_enabled`).

## Signal: `cellular.collect get_signals` and `cellular.network cell_info`
- `get_signals {time, slot}` → `{signals:[{slot, timestamp (epoch s), signal:[{ca, network_type,
  band, bandwidth, pci, earfcn, rsrp, rsrq, rssi, sinr, rscp, ecio, snr}]}]}`, one entry per 10 s.
  The rows are `cell_info`'s rows without `*_level`/`strength`, and carry no cell id or TAC.
- Row types: SA → one row 5. EN-DC → the LTE anchor as row 4, plus the NR leg as row 51 (cell
  code 51). LTE CA → rows 4 (cell code 41). No service → one row with type 0 and every metric
  -32768.
- **Where each number comes from** (fed variant lines live, compared what GL reported):
  - the PCC row's RSRP/RSRQ/SINR come from `QENG servingcell`. QCAINFO's PCC line only
    supplies the bandwidth.
  - an SCC row takes RSRP/RSRQ (plus RSSI on LTE) from its QCAINFO line, and **never a SINR**
    (-32768, LTE or NR, in every layout tried).
  - NR QCAINFO lines are read by field count: 8 fields → pci@4, rsrp@5, rsrq@6; ≥ 9 fields →
    pci@5, rsrp@9, rsrq@10. These match the baseband's 8-field NR form and its 9/12-field NR SCC
    forms (`modem/README.md`).
  - Bandwidth: an LTE QCAINFO line's resource blocks → MHz (100 → 20, 75 → 15). An NR line's code
    goes through Quectel's table (2 → 15, 11 → 90, 12 → 100 MHz).
- Levels in `cell_info` (1–4, 0 = absent), per row type: RSRP ≥ −100 → 4, ≥ −105 → 3, ≥ −110 → 2,
  else 1. SINR ≥ 12/6/0. RSRQ NR (5, 51) ≥ −12/−16/−20, LTE (4, 41) ≥ −6/−10/−14.

## SIM: `cellular.sim info` / `status`
- `info` → `sims[]{slot, bus, iccid, imsi, mcc, mnc, phone_number}`.
- `status` → `sims[]{slot, bus, iccid, carrier, name, status, ambr_dl, ambr_ul, strength, type,
  technology, apn}`. **The AMBR is only in `status`** (`libcm_sim.so` `fill_ubus_sim_status`).
- AMBR (`rg650v_get_ambr`): `+QNWCFG: "lte_ambr","<apn>",<dl>,<ul>` in kbps → ÷1000 → Mbps.
  `+QNWCFG: "nr5g_ambr","<dnn>",<unit>,<dl>,<unit>,<ul>` → value × 4^(unit−1) kbps ÷ 1000. GL takes
  the line whose APN is the SIM's data APN (from `AT+CGDCONT?`) and skips `ims` lines. With no APN
  known it reports no `ambr_*` at all.
- `AT+QSIMTYPE="slot2"` → `+QSIMTYPE: "slot2",<n>`: 1 = slot 2 is eSIM (`rg650v_slot_is_esim`).
  Slot 1 is never asked.

## Bands: `glc modem set_band_config`
- `{bus, slot, band_enable, network_mode, band_list:{LTE, "NR-NSA", "NR-SA"}}`. For the **current
  slot** it applies at once. It queries the three lists first and writes only what differs:
  - AUTO + all lists → `mode_pref` AUTO, `nr5g_disable_mode` 0, the lists.
  - AUTO/NR5G + **empty NR-SA** → `mode_pref` LTE:NR5G, `nr5g_disable_mode` **1**, no `nr5g_band`.
  - **empty NR-NSA** → LTE:NR5G, mode **2**, no `nsa_nr5g_band`.
  - LTE → `mode_pref` LTE, disable 2 then 1.
  - NR5G re-sends `mode_pref` NR5G after the cycle.
  - `band_enable:false` writes back every band the module supports (`/lib/modem_data/modem_list.json`).
  - Every apply ends with `AT+CFUN=4` → `AT+CFUN=1` and a redial.
  - A `band_list` without the NR-SA key applies nothing.
- The redial, as AT, after the radio cycle: `QNWPREFCFG="mode_pref"?`, `"roam_pref",255`,
  `QNWCFG="data_roaming",0`, `CRSM` (EF_AD), `QMBNCFG="list"`, `CGDCONT?`, `CGACT?`, `CGPADDR=1`,
  `CGATT?` (polled every 2 s until attached), then **`AT+CGACT=0,1`**: GL tears context 1 down
  itself. Bringing the call back up goes over QMI, which doesn't exist here, so the context stays
  down for the rest of the run.
- For the **other slot** it stores the config and sends no AT. `libcmcm.so` restores a slot's
  stored config whenever it dials that slot ("bus[cpu] slot[2] band set success"), under that
  slot's subscription.
- `tests/test_stack.py` closes the loop: an empty NR-SA write → GL sends `nr5g_disable_mode,1` →
  the model falls back to LTE → GL's sampler reports LTE.

## glc under qemu-user
- `glc` `read()`s 4096 bytes at a time into a `calloc(CONTENT_LENGTH+1)` buffer. The real kernel
  tolerates the overrun, but qemu-user turns it into `EFAULT` (GL answers -32700). `mudi-emu glc`
  pads `CONTENT_LENGTH` to ≥ 8192.
- The reply is `"<code> <json>"` after the CGI headers.

## Logging
- GL's `syslog()` lines reach `<workdir>/syslog.txt` through `/dev/log`.
  `cellular.status set_cellular_log_level {level}` answers Success, but the parsers' debug lines
  (`parse_qcainfo_line ok: …`) still did not appear at level 7. That is why the QCAINFO facts
  above were measured by feeding variants and comparing what GL reported.
