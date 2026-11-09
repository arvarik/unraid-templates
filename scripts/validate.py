#!/usr/bin/env python3
"""Validate Unraid Community Applications repository metadata and templates."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

USER_AGENT = "contrack-unraid-template-validator/1.0"

PROFILE_ALLOWED_TAGS = {
    "Profile",
    "Icon",
    "WebPage",
    "Forum",
    "Discord",
    "DonateLink",
    "DonateText",
    "Facebook",
    "Photo",
    "Reddit",
    "Twitter",
    "Video",
}

CONTAINER_ALLOWED_TAGS = {
    "Name",
    "Repository",
    "Registry",
    "Network",
    "Shell",
    "Privileged",
    "Support",
    "Project",
    "ReadMe",
    "Overview",
    "Description",
    "Category",
    "WebUI",
    "TemplateURL",
    "Icon",
    "Icon-azure",
    "Icon-black",
    "Icon-gray",
    "Icon-white",
    "ExtraParams",
    "ExtraSearchTerms",
    "Requires",
    "Changes",
    "Date",
    "Beta",
    "MinVer",
    "MaxVer",
    "Deprecated",
    "DeprecatedMaxVer",
    "License",
    "Licence",
    "Screenshot",
    "Video",
    "Photo",
    "Discord",
    "Reddit",
    "Facebook",
    "Twitter",
    "DonateLink",
    "DonateText",
    "DefaultTagDescription",
    "Branch",
    "MyIP",
    "Config",
}

CONTAINER_REQUIRED_TAGS = (
    "Name",
    "Repository",
    "Registry",
    "Network",
    "Privileged",
    "Support",
    "Project",
    "ReadMe",
    "Overview",
    "Category",
    "WebUI",
    "TemplateURL",
    "Icon",
)

VALID_TOP_CATEGORIES = {
    "AI",
    "Backup",
    "Cloud",
    "Crypto",
    "Downloaders",
    "Drivers",
    "GameServers",
    "HomeAutomation",
    "MediaApp",
    "MediaServer",
    "Network",
    "Other",
    "Productivity",
    "Security",
    "Spotlight",
    "Status",
    "Tools",
}

PLACEHOLDER_PATTERNS = (
    "YOUR_GITHUB_USERNAME",
    "YOUR_REPO_NAME",
    "YOUR_PLUGIN_REPO",
    "YOUR_SUPPORT_TOPIC",
    "YOUR_INVITE_CODE",
    "YOUR_YOUTUBE_VIDEO_ID",
    "REPLACE ME",
    "[year]",
    "[fullname]",
)


def curl_head_or_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    head_only: bool = True,
    attempts: int = 4,
    retry_404: bool = False,
) -> tuple[int, dict[str, str], str]:
    retry_codes = {429, 500, 502, 503, 504} | ({404} if retry_404 else set())
    last_err = ""
    for attempt in range(attempts):
        hdr_cmd = ["curl", "-sS", "-L", "--max-time", "15", "-A", USER_AGENT, "-D", "-"]
        if head_only:
            hdr_cmd.append("-I")
        for k, v in (headers or {}).items():
            hdr_cmd.extend(["-H", f"{k}: {v}"])
        if head_only:
            hdr_cmd.extend(["-o", "/dev/null", url])
        else:
            hdr_cmd.append(url)

        proc = subprocess.run(hdr_cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            last_err = proc.stderr.strip() or f"curl exited {proc.returncode}"
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(last_err)

        raw = proc.stdout
        blocks = re.split(r"\r?\n\r?\n", raw)
        status = 0
        parsed_headers: dict[str, str] = {}
        body = ""
        last_http_idx = -1
        for idx, blk in enumerate(blocks):
            if blk.startswith("HTTP/"):
                last_http_idx = idx
        if last_http_idx >= 0:
            lines = blocks[last_http_idx].splitlines()
            m = re.match(r"HTTP/\S+\s+(\d+)", lines[0])
            if m:
                status = int(m.group(1))
            for line in lines[1:]:
                if ":" in line:
                    hk, hv = line.split(":", 1)
                    parsed_headers[hk.strip().lower()] = hv.strip()
            if not head_only and last_http_idx + 1 < len(blocks):
                body = "\n\n".join(blocks[last_http_idx + 1 :])

        if status == 405 and head_only:
            return curl_head_or_get(
                url,
                headers=headers,
                head_only=False,
                attempts=attempts - attempt,
                retry_404=retry_404,
            )
        if status in retry_codes and attempt + 1 < attempts:
            time.sleep(2**attempt)
            last_err = f"HTTP {status}"
            continue
        return status, parsed_headers, body

    raise RuntimeError(last_err or f"Failed fetching {url}")


def check_url_200(
    url: str,
    label: str,
    errors: list[str],
    *,
    expect_image: bool = False,
    retry_404: bool = False,
) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        errors.append(f"{label}: expected https:// URL, got {url!r}")
        return
    try:
        status, hdrs, _ = curl_head_or_get(url, head_only=True, retry_404=retry_404)
    except Exception as exc:
        errors.append(f"{label}: HTTP check failed for {url}: {exc}")
        return
    if status != 200:
        errors.append(f"{label}: expected HTTP 200 for {url}, got {status}")
        return
    if expect_image:
        ctype = hdrs.get("content-type", "")
        if not ctype.startswith("image/"):
            errors.append(f"{label}: expected image/* content-type for {url}, got {ctype!r}")


def check_ghcr_manifest(repository: str, errors: list[str]) -> None:
    m = re.fullmatch(r"ghcr\.io/([a-z0-9._-]+/[a-z0-9._-]+):([A-Za-z0-9._-]+)", repository)
    if not m:
        errors.append(
            f"<Repository>: expected ghcr.io/<owner>/<name>:<tag>, got {repository!r}"
        )
        return
    image, tag = m.group(1), m.group(2)
    token_url = f"https://ghcr.io/token?scope=repository:{image}:pull"
    try:
        t_status, _, t_body = curl_head_or_get(token_url, head_only=False)
        if t_status != 200:
            errors.append(f"<Repository>: GHCR token request returned {t_status} for {repository}")
            return
        token = json.loads(t_body).get("token")
        if not token:
            errors.append(f"<Repository>: GHCR token response missing token for {repository}")
            return
        manifest_url = f"https://ghcr.io/v2/{image}/manifests/{tag}"
        accept = ", ".join(
            [
                "application/vnd.oci.image.index.v1+json",
                "application/vnd.docker.distribution.manifest.list.v2+json",
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.v2+json",
            ]
        )
        status, hdrs, _ = curl_head_or_get(
            manifest_url,
            headers={"Authorization": f"Bearer {token}", "Accept": accept},
            head_only=True,
        )
        if status != 200:
            errors.append(f"<Repository>: GHCR manifest check returned {status} for {repository}")
            return
        digest = hdrs.get("docker-content-digest", "")
        if not digest.startswith("sha256:"):
            errors.append(
                f"<Repository>: GHCR manifest missing sha256 docker-content-digest for {repository}"
            )
    except Exception as exc:
        errors.append(f"<Repository>: anonymous GHCR OCI manifest check failed for {repository}: {exc}")


def check_no_placeholders(path: Path, text: str, errors: list[str]) -> None:
    for needle in PLACEHOLDER_PATTERNS:
        if needle in text:
            errors.append(f"{path}: contains unreplaced starter placeholder {needle!r}")


def validate_profile(root: Path, errors: list[str]) -> None:
    profile_path = root / "ca_profile.xml"
    if not profile_path.is_file():
        errors.append("Missing required ca_profile.xml at repository root")
        return

    raw = profile_path.read_text(encoding="utf-8")
    check_no_placeholders(profile_path.relative_to(root), raw, errors)

    try:
        tree = ET.fromstring(raw)
    except ET.ParseError as exc:
        errors.append(f"ca_profile.xml: invalid XML: {exc}")
        return

    if tree.tag != "CommunityApplications":
        errors.append(
            f"ca_profile.xml: expected root <CommunityApplications>, got <{tree.tag}>"
        )
        return

    seen: dict[str, str] = {}
    for child in tree:
        if child.tag not in PROFILE_ALLOWED_TAGS:
            errors.append(f"ca_profile.xml: unsupported tag <{child.tag}>")
        val = (child.text or "").strip()
        if not val:
            errors.append(f"ca_profile.xml: empty <{child.tag}> tag is not allowed")
        seen[child.tag] = val

    if len(seen.get("Profile", "")) < 20:
        errors.append("ca_profile.xml: <Profile> must contain a meaningful maintainer description")
    if "Icon" not in seen:
        errors.append("ca_profile.xml: missing recommended <Icon>")
    else:
        check_url_200(seen["Icon"], "ca_profile.xml <Icon>", errors, expect_image=True)
    if "WebPage" not in seen:
        errors.append("ca_profile.xml: missing recommended <WebPage>")
    else:
        check_url_200(seen["WebPage"], "ca_profile.xml <WebPage>", errors)
    if "Forum" in seen:
        check_url_200(seen["Forum"], "ca_profile.xml <Forum>", errors)


def validate_template(
    root: Path,
    xml_path: Path,
    repo_slug: str,
    check_live_template_url: bool,
    errors: list[str],
) -> None:
    rel = xml_path.relative_to(root).as_posix()
    raw = xml_path.read_text(encoding="utf-8")
    check_no_placeholders(Path(rel), raw, errors)

    try:
        tree = ET.fromstring(raw)
    except ET.ParseError as exc:
        errors.append(f"{rel}: invalid XML: {exc}")
        return

    if tree.tag != "Container" or tree.attrib.get("version") != "2":
        errors.append(f'{rel}: root must be <Container version="2">')
        return

    tags: dict[str, str] = {}
    configs: list[ET.Element] = []
    for child in tree:
        if child.tag not in CONTAINER_ALLOWED_TAGS:
            errors.append(f"{rel}: unsupported tag <{child.tag}>")
        if child.tag == "Config":
            configs.append(child)
            continue
        val = (child.text or "").strip()
        if child.tag != "MyIP" and not val:
            errors.append(f"{rel}: empty <{child.tag}> tag is not allowed")
        tags[child.tag] = val

    for req_tag in CONTAINER_REQUIRED_TAGS:
        if not tags.get(req_tag):
            errors.append(f"{rel}: missing required non-empty <{req_tag}>")

    if tags.get("Privileged") != "false":
        errors.append(f"{rel}: <Privileged> must be 'false', got {tags.get('Privileged')!r}")

    overview = tags.get("Overview", "")
    if re.search(r"<[A-Za-z/!][^>]*>", overview):
        errors.append(f"{rel}: <Overview> must be plain text without HTML tags")

    categories = tags.get("Category", "").split()
    if not categories:
        errors.append(f"{rel}: <Category> must list at least one Unraid category")
    for cat in categories:
        if ":" not in cat:
            errors.append(f"{rel}: category token {cat!r} must include ':'")
            continue
        top = cat.split(":", 1)[0]
        if top not in VALID_TOP_CATEGORIES:
            errors.append(f"{rel}: unknown top-level Unraid category {top!r} in {cat!r}")

    expected_template_url = f"https://raw.githubusercontent.com/{repo_slug}/main/{rel}"
    if tags.get("TemplateURL") != expected_template_url:
        errors.append(
            f"{rel}: <TemplateURL> must be {expected_template_url!r}, got {tags.get('TemplateURL')!r}"
        )
    elif check_live_template_url:
        check_url_200(expected_template_url, f"{rel} <TemplateURL>", errors, retry_404=True)

    webui = tags.get("WebUI", "")
    webui_match = re.fullmatch(r"https?://\[IP\]:\[PORT:(\d+)\](?:/.*)?", webui)
    if not webui_match:
        errors.append(
            f"{rel}: <WebUI> must use http(s)://[IP]:[PORT:<container_port>], got {webui!r}"
        )
    webui_port = webui_match.group(1) if webui_match else None

    if not configs:
        errors.append(f"{rel}: must define at least one <Config> entry")

    port_targets: set[str] = set()
    has_appdata_path = False
    for idx, cfg in enumerate(configs, start=1):
        a = cfg.attrib
        for attr in ("Name", "Target", "Default", "Description", "Type", "Display", "Required", "Mask"):
            if attr not in a:
                errors.append(f"{rel}: <Config #{idx}> missing attribute {attr!r}")
        cfg_type = a.get("Type", "")
        target = a.get("Target", "")
        default = a.get("Default", "")
        if not a.get("Name", "").strip() or not target.strip() or not a.get("Description", "").strip():
            errors.append(f"{rel}: <Config #{idx}> Name, Target, and Description must be non-empty")
        if a.get("Required") not in ("true", "false"):
            errors.append(f"{rel}: <Config #{idx}> Required must be 'true' or 'false'")
        if a.get("Mask") not in ("true", "false"):
            errors.append(f"{rel}: <Config #{idx}> Mask must be 'true' or 'false'")

        if cfg_type == "Port":
            if not target.isdigit():
                errors.append(f"{rel}: Port <Config {a.get('Name')!r}> Target must be numeric")
            if a.get("Mode") not in ("tcp", "udp", "http"):
                errors.append(f"{rel}: Port <Config {a.get('Name')!r}> Mode must be tcp/udp/http")
            port_targets.add(target)
        elif cfg_type == "Path":
            if a.get("Mode") not in ("rw", "ro", "rw,slave", "ro,slave", "rw,shared", "ro,shared"):
                errors.append(f"{rel}: Path <Config {a.get('Name')!r}> has invalid Mode {a.get('Mode')!r}")
            if not default.startswith("/mnt/user/appdata/"):
                errors.append(
                    f"{rel}: Path <Config {a.get('Name')!r}> Default must start with /mnt/user/appdata/, got {default!r}"
                )
            else:
                has_appdata_path = True
        elif cfg_type == "Variable":
            if target in ("PUID", "PGID"):
                errors.append(
                    f"{rel}: bogus {target} <Config> variable; use --user 99:100 in <ExtraParams> instead"
                )
        else:
            errors.append(f"{rel}: unsupported <Config> Type {cfg_type!r}")

    if webui_port and webui_port not in port_targets:
        errors.append(
            f"{rel}: <WebUI> references [PORT:{webui_port}], but no Port <Config> has Target={webui_port!r}"
        )
    if not has_appdata_path:
        errors.append(f"{rel}: must include a Path <Config> under /mnt/user/appdata/")

    if "Icon" in tags:
        check_url_200(tags["Icon"], f"{rel} <Icon>", errors, expect_image=True)
    for url_tag in ("Project", "Support", "ReadMe", "Registry"):
        if url_tag in tags:
            check_url_200(tags[url_tag], f"{rel} <{url_tag}>", errors)
    if "Repository" in tags:
        check_ghcr_manifest(tags["Repository"], errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", "arvarik/unraid-templates"),
        help="GitHub owner/repo slug for <TemplateURL> verification",
    )
    parser.add_argument(
        "--check-live-template-url",
        action="store_true",
        default=os.environ.get("GITHUB_REF") == "refs/heads/main",
        help="Also verify <TemplateURL> returns HTTP 200 on raw.githubusercontent.com",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    errors: list[str] = []

    license_path = root / "LICENSE"
    if not license_path.is_file() or len(license_path.read_text(encoding="utf-8").strip()) < 100:
        errors.append("Missing or empty root LICENSE file")
    else:
        check_no_placeholders(Path("LICENSE"), license_path.read_text(encoding="utf-8"), errors)

    for forbidden in ("templates/example-app.xml", "plugins/example-plugin.xml", "icon.svg"):
        if (root / forbidden).exists():
            errors.append(f"Leftover starter placeholder file must be removed: {forbidden}")

    validate_profile(root, errors)

    templates_dir = root / "templates"
    template_files = sorted(templates_dir.glob("*.xml")) if templates_dir.is_dir() else []
    if not template_files:
        errors.append("No template XML files found under templates/*.xml")
    for xml_path in template_files:
        validate_template(
            root,
            xml_path,
            args.repo,
            args.check_live_template_url,
            errors,
        )

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"\nValidation failed with {len(errors)} error(s).", file=sys.stderr)
        return 1

    print(
        f"Validated ca_profile.xml and {len(template_files)} template(s): 0 errors, 0 warnings."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
