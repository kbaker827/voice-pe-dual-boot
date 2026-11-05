"""Tests for scripts/patch_sources.py. Run with: python3 -m unittest discover tests"""

import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from patch_sources import (  # noqa: E402
    PatchError,
    apply_all,
    patch_app_c,
    patch_sdkconfig,
    patch_voice_yaml,
    write_secrets,
)

APP_C = """#include "app.h"
#include "esp_log.h"

static void on_button_double_press(void) {
    ESP_LOGI(TAG, "button double-press ignored");
}
"""

VOICE_YAML = """esphome:
  name: home-assistant-voice

esp32:
  framework:
    type: esp-idf
    sdkconfig_options:
      CONFIG_EXISTING: "y"

wifi:
  id: wifi_id

ota:
  - platform: esphome
    id: ota_esphome

binary_sensor:
  # Center Button. Used for many things
  - platform: gpio
    on_multi_click:
      - timing:
          - ON for at most 1s

button:
  - platform: factory_reset
"""


class PatchAppCTest(unittest.TestCase):
    def test_switches_to_esphome_on_double_press(self):
        result = patch_app_c(APP_C)
        self.assertIn('#include "ota_switch.h"', result)
        self.assertIn("    dualboot_switch_to_esphome();\n", result)
        self.assertNotIn("double-press ignored", result)

    def test_second_run_changes_nothing(self):
        once = patch_app_c(APP_C)
        self.assertEqual(once, patch_app_c(once))

    def test_fails_when_handler_has_changed_upstream(self):
        with self.assertRaises(PatchError):
            patch_app_c('#include "app.h"\n')


class PatchSdkconfigTest(unittest.TestCase):
    def test_turns_off_signing_and_rollback_and_sets_table(self):
        result = patch_sdkconfig("CONFIG_IDF_TARGET=\"esp32s3\"")
        self.assertTrue(result.startswith('CONFIG_IDF_TARGET="esp32s3"\n'))
        for line in (
            'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions_dualboot.csv"',
            "CONFIG_PARTITION_TABLE_OFFSET=0x8000",
            "CONFIG_SECURE_SIGNED_APPS_NO_SECURE_BOOT=n",
            "CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT=n",
            "CONFIG_SECURE_BOOT_BUILD_SIGNED_BINARIES=n",
            "CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=n",
        ):
            self.assertIn(line + "\n", result)

    def test_second_run_changes_nothing(self):
        once = patch_sdkconfig("A=1\n")
        self.assertEqual(once, patch_sdkconfig(once))


class PatchVoiceYamlTest(unittest.TestCase):
    def setUp(self):
        self.result = patch_voice_yaml(VOICE_YAML)

    def test_removes_only_the_ota_block(self):
        self.assertNotIn("ota_esphome", self.result)
        self.assertNotIn("\nota:", self.result)
        self.assertIn("binary_sensor:", self.result)

    def test_adds_partition_table_and_quoted_options(self):
        self.assertIn("esp32:\n  partitions: partitions_dualboot.csv\n", self.result)
        self.assertIn('      CONFIG_PARTITION_TABLE_OFFSET: "0x8000"\n', self.result)
        self.assertIn('      CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE: "n"\n', self.result)
        self.assertIn('      CONFIG_EXISTING: "y"\n', self.result)

    def test_adds_include_wifi_and_template_button(self):
        self.assertIn("esphome:\n  includes:\n    - ota_switch.h\n", self.result)
        self.assertIn(
            "wifi:\n  ssid: !secret wifi_ssid\n  password: !secret wifi_password\n",
            self.result,
        )
        self.assertIn('button:\n  - platform: template\n    name: "Switch to Muse"\n', self.result)

    def test_adds_gesture_at_the_existing_timing_indent(self):
        self.assertIn(
            "    on_multi_click:\n"
            "      - timing:\n"
            "          - ON for at most 1s\n"
            "          - OFF for at most 0.25s\n"
            "          - ON for at least 3s\n",
            self.result,
        )

    def test_second_run_changes_nothing(self):
        self.assertEqual(self.result, patch_voice_yaml(self.result))

    def test_fails_when_a_block_is_missing(self):
        with self.assertRaises(PatchError):
            patch_voice_yaml(VOICE_YAML.replace("wifi:\n", "network:\n"))


class ApplyAllTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.shared = os.path.join(temp.name, "shared")
        self.muse = os.path.join(temp.name, "muse")
        self.esphome = os.path.join(temp.name, "esphome")
        self.sdkconfig = os.path.join(
            self.muse, "esp32", "devices", "sdkconfig.home-assistant-voice"
        )
        self.app_c = os.path.join(self.muse, "esp32", "main", "app.c")
        self.yaml = os.path.join(self.esphome, "home-assistant-voice.yaml")
        files = {
            os.path.join(self.shared, "partitions_dualboot.csv"): "csv\n",
            os.path.join(self.shared, "ota_switch.h"): "header\n",
            self.sdkconfig: "A=1\n",
            self.app_c: APP_C,
            self.yaml: VOICE_YAML,
        }
        for path, content in files.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)

    def _read(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_patches_both_repos_and_copies_shared_files(self):
        apply_all(self.shared, self.muse, self.esphome, "net", "password")
        self.assertIn("dualboot_switch_to_esphome", self._read(self.app_c))
        self.assertIn("partitions_dualboot.csv", self._read(self.yaml))
        for path in (
            os.path.join(self.muse, "esp32", "partitions_dualboot.csv"),
            os.path.join(self.muse, "esp32", "main", "ota_switch.h"),
            os.path.join(self.esphome, "ota_switch.h"),
            os.path.join(self.esphome, "secrets.yaml"),
        ):
            self.assertTrue(os.path.exists(path), path)

    def test_writes_nothing_if_a_later_patch_fails(self):
        with open(self.yaml, "w", encoding="utf-8") as handle:
            handle.write("not: the expected config\n")
        with self.assertRaises(PatchError):
            apply_all(self.shared, self.muse, self.esphome, "net", "password")
        self.assertEqual(self._read(self.sdkconfig), "A=1\n")
        self.assertEqual(self._read(self.app_c), APP_C)
        self.assertFalse(os.path.exists(os.path.join(self.esphome, "secrets.yaml")))

    def test_requires_a_network_name_for_new_secrets(self):
        with self.assertRaises(PatchError):
            apply_all(self.shared, self.muse, self.esphome, "", "")
        self.assertEqual(self._read(self.sdkconfig), "A=1\n")


class WriteSecretsTest(unittest.TestCase):
    def test_escapes_values_for_yaml(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "secrets.yaml")
            write_secrets(path, 'my "net"', "pa\\ss: #1")
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        # JSON strings are valid YAML double-quoted strings.
        self.assertEqual(json.loads(lines[0].split(": ", 1)[1]), 'my "net"')
        self.assertEqual(json.loads(lines[1].split(": ", 1)[1]), "pa\\ss: #1")

    @unittest.skipIf(os.name == "nt", "POSIX file permissions only")
    def test_only_the_owner_can_read_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "secrets.yaml")
            write_secrets(path, "net", "password")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_never_overwrites_an_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "secrets.yaml")
            write_secrets(path, "first", "password")
            with self.assertRaises(FileExistsError):
                write_secrets(path, "second", "password")


if __name__ == "__main__":
    unittest.main()
