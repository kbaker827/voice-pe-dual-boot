#!/usr/bin/env python3
"""Apply the dual-boot changes to clones of the Muse SDK and Voice PE config.

Safe to run repeatedly: files that are already patched are left alone. If an
upstream file no longer looks as expected, it stops before writing to either repo.

Wi-Fi details come from the WIFI_SSID and WIFI_PASSWORD environment variables,
so they never appear in a process list.
"""

import argparse
import json
import os
import re
import shutil
import sys

SHARED_CSV = "partitions_dualboot.csv"
SHARED_HEADER = "ota_switch.h"
SDKCONFIG_MARKER = "# voice-pe-dual-boot settings"

SDKCONFIG_LINES = (
    SDKCONFIG_MARKER,
    f'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="{SHARED_CSV}"',
    "CONFIG_PARTITION_TABLE_OFFSET=0x8000",
    "CONFIG_SECURE_SIGNED_APPS_NO_SECURE_BOOT=n",
    "CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT=n",
    "CONFIG_SECURE_BOOT_BUILD_SIGNED_BINARIES=n",
    "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=n",
)

DOUBLE_PRESS_HANDLER = re.compile(
    r"static void on_button_double_press\(void\) \{\n"
    r'\s*ESP_LOGI\(TAG, "button double-press ignored"\);\n'
    r"\}"
)
DOUBLE_PRESS_REPLACEMENT = (
    "static void on_button_double_press(void) {\n"
    "    dualboot_switch_to_esphome();\n"
    "}"
)

TEMPLATE_BUTTON = (
    "  - platform: template",
    '    name: "Switch to Muse"',
    "    on_press:",
    "      - lambda: 'dualboot_switch_to_muse();'",
)

GESTURE = (
    "- timing:",
    "    - ON for at most 1s",
    "    - OFF for at most 0.25s",
    "    - ON for at least 3s",
    "  then:",
    "    - if:",
    "        condition:",
    "          lambda: return !id(init_in_progress);",
    "        then:",
    "          - lambda: 'dualboot_switch_to_muse();'",
)


class PatchError(Exception):
    """An upstream file doesn't match what this script expects."""


def patch_app_c(text):
    """Return app.c with the double press wired to the switch."""
    if "dualboot_switch_to_esphome" in text:
        return text
    patched, count = DOUBLE_PRESS_HANDLER.subn(DOUBLE_PRESS_REPLACEMENT, text)
    if count != 1:
        raise PatchError("app.c: on_button_double_press() has changed upstream")
    first_include = re.search(r'^#include ".*"$', patched, flags=re.MULTILINE)
    if first_include is None:
        raise PatchError("app.c: no #include line found")
    end = first_include.end()
    return f'{patched[:end]}\n#include "{SHARED_HEADER}"{patched[end:]}'


def patch_sdkconfig(text):
    """Return the board's sdkconfig with the dual-boot settings appended."""
    if SDKCONFIG_MARKER in text:
        return text
    separator = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{separator}\n" + "\n".join(SDKCONFIG_LINES) + "\n"


def _find_line(lines, pattern, what, start=0):
    """Return the index of the single line after `start` matching `pattern`."""
    matches = [
        index
        for index, line in enumerate(lines)
        if index >= start and re.fullmatch(pattern, line.rstrip())
    ]
    if len(matches) != 1:
        raise PatchError(f"YAML: expected one {what}, found {len(matches)}")
    return matches[0]


def _insert_after(lines, index, new_lines):
    return [*lines[: index + 1], *new_lines, *lines[index + 1 :]]


def _insert_after_key(lines, pattern, what, new_lines):
    return _insert_after(lines, _find_line(lines, pattern, what), new_lines)


def _remove_top_level_block(lines, key):
    start = _find_line(lines, f"{key}:", f"top-level {key}: block")
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][0].isspace()):
        end += 1
    return [*lines[:start], *lines[end:]]


def _insert_gesture(lines):
    anchors = [i for i, line in enumerate(lines) if "# Center Button" in line]
    if len(anchors) != 1:
        raise PatchError("YAML: expected one centre button comment")
    # Only the centre button's own on_multi_click may come after its comment,
    # so stop looking at the next sensor by taking the first match.
    click = next(
        (
            i
            for i in range(anchors[0], len(lines))
            if lines[i].strip() == "on_multi_click:"
        ),
        None,
    )
    if click is None:
        raise PatchError("YAML: centre button has no on_multi_click")
    timing = next(
        (
            i
            for i in range(click + 1, len(lines))
            if lines[i].strip().startswith("- timing:")
        ),
        None,
    )
    if timing is None:
        raise PatchError("YAML: on_multi_click has no timing entries")
    indent = lines[timing][: len(lines[timing]) - len(lines[timing].lstrip())]
    return _insert_after(lines, click, [indent + line for line in GESTURE])


def patch_voice_yaml(text):
    """Return the Voice PE config with OTA removed and the switch added."""
    if SHARED_CSV in text:
        return text
    lines = _remove_top_level_block(text.split("\n"), "ota")
    lines = _insert_after_key(
        lines, "esphome:", "esphome: block", ["  includes:", f"    - {SHARED_HEADER}"]
    )
    lines = _insert_after_key(
        lines, "esp32:", "esp32: block", [f"  partitions: {SHARED_CSV}"]
    )
    lines = _insert_after_key(
        lines,
        r"\s+sdkconfig_options:",
        "sdkconfig_options: block",
        [
            '      CONFIG_PARTITION_TABLE_OFFSET: "0x8000"',
            '      CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE: "n"',
        ],
    )
    lines = _insert_after_key(
        lines,
        "wifi:",
        "wifi: block",
        ["  ssid: !secret wifi_ssid", "  password: !secret wifi_password"],
    )
    lines = _insert_after_key(lines, "button:", "button: block", list(TEMPLATE_BUTTON))
    return "\n".join(_insert_gesture(lines))


def write_secrets(path, ssid, password):
    """Create secrets.yaml, readable only by its owner. Never overwrites."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        # A JSON string is also a valid YAML double-quoted string.
        handle.write(f"wifi_ssid: {json.dumps(ssid, ensure_ascii=False)}\n")
        handle.write(f"wifi_password: {json.dumps(password, ensure_ascii=False)}\n")


def _plan_patch(path, patch):
    """Return (path, new text), with new text None if the file needs no change."""
    with open(path, encoding="utf-8") as handle:
        original = handle.read()
    patched = patch(original)
    return path, (None if patched == original else patched)


def _write_patch(path, text):
    if text is None:
        print(f"  already patched: {path}")
        return
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(f"  patched: {path}")


def _exclude_from_git(repo, name):
    """Keep a local file out of commits made from this clone."""
    exclude = os.path.join(repo, ".git", "info", "exclude")
    if not os.path.isfile(exclude):
        return
    with open(exclude, encoding="utf-8") as handle:
        if name in handle.read().splitlines():
            return
    with open(exclude, "a", encoding="utf-8") as handle:
        handle.write(f"\n{name}\n")


def apply_all(shared, muse, esphome, ssid, password):
    """Patch both repos, writing nothing unless every patch can be applied."""
    esp32 = os.path.join(muse, "esp32")
    secrets = os.path.join(esphome, "secrets.yaml")
    if not ssid and not os.path.exists(secrets):
        raise PatchError("WIFI_SSID is not set and there's no secrets.yaml yet")

    plans = [
        _plan_patch(
            os.path.join(esp32, "devices", "sdkconfig.home-assistant-voice"),
            patch_sdkconfig,
        ),
        _plan_patch(os.path.join(esp32, "main", "app.c"), patch_app_c),
        _plan_patch(os.path.join(esphome, "home-assistant-voice.yaml"), patch_voice_yaml),
    ]
    for path, text in plans:
        _write_patch(path, text)

    copies = (
        (SHARED_CSV, esp32),
        (SHARED_HEADER, os.path.join(esp32, "main")),
        (SHARED_CSV, esphome),
        (SHARED_HEADER, esphome),
    )
    for name, folder in copies:
        shutil.copyfile(os.path.join(shared, name), os.path.join(folder, name))

    _exclude_from_git(esphome, "secrets.yaml")
    if os.path.exists(secrets):
        print(f"  keeping existing: {secrets}")
        return
    write_secrets(secrets, ssid, password)
    print(f"  created: {secrets}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shared", required=True, help="this repo's folder")
    parser.add_argument("--muse", required=True, help="muse-gadget-sdk clone")
    parser.add_argument("--esphome", required=True, help="home-assistant-voice-pe clone")
    args = parser.parse_args()
    try:
        apply_all(
            args.shared,
            args.muse,
            args.esphome,
            os.environ.get("WIFI_SSID", ""),
            os.environ.get("WIFI_PASSWORD", ""),
        )
    except (PatchError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
