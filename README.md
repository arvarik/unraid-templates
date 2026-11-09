# Unraid Templates for Contrack

Official Unraid Community Applications template repository for [Contrack](https://github.com/arvarik/contrack), a self-hosted personal CRM.

## Templates

| App | Template | Image | Default Port | Appdata Path |
| --- | --- | --- | --- | --- |
| [Contrack](https://github.com/arvarik/contrack) | [`templates/contrack.xml`](templates/contrack.xml) | `ghcr.io/arvarik/contrack:latest` | `3210` | `/mnt/user/appdata/contrack` |

## Install on Unraid

### From Community Applications

1. Open the **Apps** tab in the Unraid WebGUI.
2. Search for **Contrack** and select **Install**.
3. Review the port (`3210`) and appdata path (`/mnt/user/appdata/contrack`), then select **Apply**.
4. Open the WebUI (`http://<unraid-ip>:3210`) to create the administrator account.

### Manual template install (before Community Applications listing is live)

Run on your Unraid host (SSH or WebGUI Terminal) to add the template under **Docker -> Add Container -> Template -> User templates**:

```bash
curl -fsSL https://raw.githubusercontent.com/arvarik/unraid-templates/main/templates/contrack.xml \
  -o /boot/config/plugins/dockerMan/templates-user/my-Contrack.xml
```

## Notes for Unraid

- **Permissions (`99:100`):** Unraid creates `/mnt/user/appdata/contrack` owned by `nobody:users` (`99:100`). The template sets `<ExtraParams>--user 99:100 -e NODE_COMPILE_CACHE=/tmp/compile-cache</ExtraParams>` so Contrack writes `curator.db`, `secret.key`, `uploads/`, and `backups/` directly as `99:100` (enforcing `0600` on files and `0700` on subdirectories) and keeps Node's V8/TypeScript compile cache writable under `/tmp/compile-cache`.
- **Reverse proxy:** When exposing Contrack behind Caddy, nginx, Traefik, or Cloudflare Tunnel over HTTPS, set **Public URL** (`PUBLIC_URL`, e.g. `https://crm.example.com` with no path) and **Reverse Proxy Hops** (`TRUST_PROXY_HOPS`, e.g. `1`). See the [Self-hosting guide](https://github.com/arvarik/contrack/blob/main/docs/self-hosting.md#behind-a-reverse-proxy).
- **Forum support link:** `<Support>` in `templates/contrack.xml` currently points to `https://github.com/arvarik/contrack/issues`. Once a dedicated `[Support] Contrack` topic is posted in Unraid Forums (**Docker Containers**), replace `<Support>` in `templates/contrack.xml` and add `<Forum>` in `ca_profile.xml` with that thread URL.

## Validation

Run locally before opening a pull request:

```bash
xmllint --noout ca_profile.xml templates/*.xml
python3 scripts/validate.py
```

## License

Templates and metadata in this repository are licensed under the [MIT License](LICENSE). Contrack itself is licensed under `AGPL-3.0-only`.
