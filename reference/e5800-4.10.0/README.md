# GL files from e5800-4.10.0

Small copies of GL files, kept in the repo so they can be read and cited without the image.
Each one is a verbatim copy of an image file or derived from the image. `mudi-emu reference
check` compares them with the extracted image, `tests/test_reference.py` runs that check when
the image is there, and `mudi-emu reference refresh` rewrites them from it. Nothing else from
the image is committed. The source list lives in `lib/mudi_emu/reference.py`.

| file | from the image | what it shows |
|---|---|---|
| `abfota_update` | `/usr/bin/abfota_update` | every E5800 upgrade path ends here; with `-k` it keeps exactly `/lib/upgrade/keep.d/*` (`docs/re-notes-4.10.md`) |
| `gl-ngx-session.lua` | `/usr/sbin/gl-ngx-session` | GL's session daemon (an eco script): the ubus proxy behind `oui.ubus`, websocket push, `gl-session` ubus methods |
| `gl.conf` | `/etc/nginx/conf.d/gl.conf` | the admin's nginx server block (`/rpc`, `/ws`, `/views`, one worker) |
| `oui_nginx.conf` | `/etc/nginx/oui_nginx.conf` | the Lua hooks nginx loads |
| `oui-lib-rpc.lua` | `/usr/lib/lua/oui/rpc.lua` | `/rpc` dispatch, access rules (dotted objects, local loop-back) |
| `oui-lib-ubus.lua` | `/usr/lib/lua/oui/ubus.lua` | `oui.ubus.call`, a cosocket to gl-ngx-session's proxy |
| `oui-rpc.lua` | `/usr/share/gl-ngx/oui-rpc.lua` | the `/rpc` content handler |
| `oui-ws.lua` | `/usr/share/gl-ngx/oui-ws.lua` | `/ws` subscribe/push |
| `websocket-cellular.lua` | `/usr/share/gl-ngx/websocket/cellular.lua` | the `cellular.*` websocket merge (`simcard.dial_status = net.status`) |
| `menu-cellular-detail.json`, `menu-internet.json` | `/usr/share/oui/menu.d/` | menu entry shape |
| `menu-views.txt` | `ls /usr/share/oui/menu.d` | every menu file GL ships |
| `rpc-objects.txt` | `ls /usr/lib/oui-httpd/rpc` | every `/rpc` object (Lua plugins and `.so` for glc) |
| `theme-vars.txt` | every `--name:` in `/www/theme/base.css` + `{default,classic,dark}/index.css.gz` | the CSS variables GL's theme defines: lint page CSS against it |
