# Voice PE Dual-Boot: Muse ⇄ Home Assistant on one device

A design for running **both** firmwares on a single Home Assistant Voice
Preview Edition and choosing between them from the device itself:

- **Slot A (`ota_0`)** — Muse gadget firmware (Meta's open-source
  `facebookincubator/muse-gadget-sdk`, `home-assistant-voice` board build)
- **Slot B (`ota_1`)** — stock-style ESPHome firmware (Home Assistant voice
  satellite, wake word, Assist)

Only one runs at a time. A button gesture flips the ESP-IDF boot partition and
reboots into the other assistant (~5 seconds).

**Status: design only.** It was checked against the SDK, ESPHome and ESP-IDF
source on 2026-11-05, but has not been built or run on a device. Work through
[Test first](#test-first) before relying on it.

## Setup

These steps are untested, like the rest of this design. Step 4 erases the device,
so read [What you give up](#what-you-give-up--read-before-building) first.

**You'll need:**

- A Voice PE and a USB-C cable that carries data
- A computer running macOS or Linux, which the Muse SDK requires
- [ESP-IDF v6.0.3](https://docs.espressif.com/projects/esp-idf/en/v6.0.3/esp32s3/get-started/linux-macos-setup.html),
  installed at `~/esp/esp-idf-v6` with `esp32s3` support
- ESPHome 2026.9.0 or newer (`pip install esphome`)
- An [SDK token](https://gadgets.muse.ai/settings/sdk-tokens) and the Muse app
- Home Assistant

### 1. Get the sources

Clone all three repos into the same folder, and run later steps from that folder
unless they say otherwise:

```bash
git clone https://github.com/kbaker827/voice-pe-dual-boot.git
git clone https://github.com/facebookincubator/muse-gadget-sdk.git
git clone https://github.com/esphome/home-assistant-voice-pe.git
```

### 2. Build Muse

Copy in the shared files:

```bash
cp voice-pe-dual-boot/partitions_dualboot.csv muse-gadget-sdk/esp32/
cp voice-pe-dual-boot/ota_switch.h muse-gadget-sdk/esp32/main/
```

Then make two edits in `muse-gadget-sdk/esp32`:

1. Append the Muse settings from [Build changes](#build-changes) to
   `devices/sdkconfig.home-assistant-voice`.
2. In `main/app.c`, add `#include "ota_switch.h"` and change the double-press
   handler to:

   ```c
   static void on_button_double_press(void) {
       dualboot_switch_to_esphome();
   }
   ```

Build it, with a clean build folder so the new settings apply:

```bash
. ~/esp/esp-idf-v6/export.sh
cd muse-gadget-sdk/esp32
rm -rf build-home-assistant-voice
tools/board.sh home-assistant-voice build
cd ../..
```

### 3. Build ESPHome

Copy in the shared files:

```bash
cp voice-pe-dual-boot/partitions_dualboot.csv home-assistant-voice-pe/
cp voice-pe-dual-boot/ota_switch.h home-assistant-voice-pe/
```

Then edit `home-assistant-voice-pe/home-assistant-voice.yaml`, adding to the
blocks already there rather than creating duplicates:

1. Delete the `ota:` block.
2. Add the `esp32:` settings from [Build changes](#build-changes).
3. Add the `includes:` entry, template button and `on_multi_click` entry from
   [The switch](#the-switch).
4. Add your Wi-Fi network under the `wifi:` block. This base config has no
   setup-over-Bluetooth step:

   ```yaml
   wifi:
     ssid: !secret wifi_ssid
     password: !secret wifi_password
   ```

Put those values in `home-assistant-voice-pe/secrets.yaml`, and keep that file out
of any public repo:

```yaml
wifi_ssid: "your network"
wifi_password: "your password"
```

In a **new terminal**, without the ESP-IDF environment loaded, build it and list
the files you'll flash:

```bash
cd home-assistant-voice-pe
esphome compile home-assistant-voice.yaml
find .esphome/build -name firmware.ota.bin -o -name bootloader.bin
cd ..
```

### 4. Flash the device

Connect the Voice PE over USB. If esptool can't connect, unplug it, then hold the
centre button while plugging it back in.

Run the two commands under [First flash](#first-flash-usb-one-time), replacing
the port with yours and using these files:

| In the command | File |
|---|---|
| `bootloader.bin` | The `bootloader.bin` found in step 3 |
| `partition-table.bin` | `muse-gadget-sdk/esp32/build-home-assistant-voice/partition_table/partition-table.bin` |
| `muse-gadget.bin` | `muse-gadget-sdk/esp32/build-home-assistant-voice/muse-gadget.bin` |
| `firmware.ota.bin` | The `firmware.ota.bin` found in step 3 |

Always flash with esptool. `tools/board.sh ... flash` only writes Muse's files and
can reset which slot boots.

### 5. Set up Muse

The device starts in Muse. Open its console, send your token as a line in the form
`>sdk_token=mgst_…`, and wait for `@sdk_token ok`. Press `Ctrl-]` to quit.

```bash
cd muse-gadget-sdk/esp32
tools/board.sh home-assistant-voice monitor /dev/ttyACM0
cd ../..
```

In the Muse app, turn on **Settings > Devices > Developer mode**, then go to
**Settings > Devices > Add Device**. Choose `MuseGadget-ha-voice-XXXXXX`, and
briefly press the centre button when asked to confirm. Muse connects to Wi-Fi
through this setup.

The token is stored in the shared `nvs` partition, so send it again after an
ESPHome factory reset or a full erase.

### 6. Set up ESPHome

Double-press the centre button to switch. The first boot may update the XMOS
firmware, so leave the device powered until it finishes.

Add the device in Home Assistant under **Settings > Devices & services**. If it
isn't discovered, add the ESPHome integration with the address
`home-assistant-voice-XXXXXX.local`, using the end of the device's MAC address.

### 7. Check it works

Switch back to Muse with a click followed by a 3-second hold, or with the **Switch
to Muse** button in Home Assistant. Then work through [Test first](#test-first).

To update one firmware later, rebuild it and flash only its slot, as described
under [What you give up](#what-you-give-up--read-before-building).

## Why this works on this hardware

- The Voice PE is an ESP32-S3 with **16 MB flash** — both firmwares are
  ~2 MB today, so two 7 MB slots leave huge headroom.
- Both firmwares are **ESP-IDF underneath** (the Muse SDK natively; the
  Voice PE's ESPHome build uses the esp-idf framework), so they share the
  standard IDF bootloader + `otadata` boot-selection mechanism.
- Both firmwares drive the same mics, speaker and LED ring through the
  **XMOS XU316 audio front end**, which keeps its own firmware. Muse never
  reflashes it, but **ESPHome does**: it updates the XMOS automatically when the
  version differs from the one in its YAML (1.3.1 today). Confirm Muse works
  with that version, and let the first ESPHome boot finish before switching.

## Partition layout

One custom table, used by **both** builds (`partitions_dualboot.csv`):

| Partition | Type | Offset | Size | Purpose |
|---|---|---|---|---|
| nvs | data | 0x9000 | 0x6000 | Settings for both firmwares |
| otadata | data | 0xF000 | 0x2000 | Boot-slot selection (the switch) |
| phy_init | data | 0x11000 | 0x1000 | RF calibration |
| muse | app `ota_0` | 0x20000 | 7 MB | Muse gadget firmware |
| esphome | app `ota_1` | 0x720000 | 7 MB | ESPHome firmware |
| storage | data | 0xE20000 | ~1.9 MB | Spare (shared files, future use) |

Two things must match across both builds, or nothing boots:

1. **Partition table offset 0x8000** (the IDF standard). The Muse SDK
   defaults to `0x10000` (their Secure Boot V2 bootloader is bigger), so
   override it — see below. Use a plain dev build; do **not** use the SDK's
   release/Secure-Boot flow, which burns eFuses.
2. **The same CSV.** The Muse SDK takes it via
   `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME`; ESPHome via the `esp32:
   partitions:` option (ESPHome supports a CSV file or inline list there).

`otadata` must be blank on first boot: with no boot record, the bootloader
starts `ota_0` (Muse). The [first flash](#first-flash-usb-one-time) erases the
chip to guarantee this.

## The switch

The whole trick is one IDF call — `esp_ota_set_boot_partition()` — then a
reboot. `ota_switch.h` (in this folder) wraps it and logs the reason if the
switch fails, in which case the current firmware keeps running. Wire it to a
gesture in each firmware.

**ESPHome side: click, then press and hold for 3 seconds.** Plain long holds are
taken: 1 second fires a Home Assistant event, 10 seconds starts the
factory-reset warning, and 22 seconds confirms it. A hold-to-switch gesture would
reboot before the factory-reset hold could finish. Add the header and a template
button, for switching from Home Assistant, to the Voice PE YAML:

```yaml
esphome:
  includes:
    - ota_switch.h   # copy of the file in this folder, next to the YAML

button:
  - platform: template
    name: "Switch to Muse"
    on_press:
      - lambda: 'dualboot_switch_to_muse();'
```

Then add this entry by hand to the list under the centre button's
`on_multi_click`, at the same indent as the existing `- timing:` entries:

```yaml
      - timing:
          - ON for at most 1s
          - OFF for at most 0.25s
          - ON for at least 3s
        then:
          - if:
              condition:
                lambda: return !id(init_in_progress);
              then:
                - lambda: 'dualboot_switch_to_muse();'
```

`init_in_progress` stays true until the device reaches Home Assistant or 10
minutes pass, so the gesture does nothing during that time. The YAML's existing
1-second long-press event may also fire during the hold, which is harmless.

**Muse side: double press.** In `esp32/main/app.c`, `on_button_double_press()`
is unused, so call `dualboot_switch_to_esphome()` from it (include the same
header). **Don't use the long press:** a 5-second hold resets Muse's setup. The
button is only wired up when `CONFIG_MUSE_ENABLED` is off, so first confirm that
a double press logs `button double-press ignored` on this board. The LED ring
backend is already theirs, so you can flash the ring before rebooting as feedback.

⚠️ **Don't make the selector a hold-at-power-on check.** The centre button
is GPIO 0 — the S3's boot-strapping pin. Holding it during reset puts the
chip in ROM download mode instead of reading it as input. In-firmware
switching sidesteps this entirely, and download mode remains your recovery
path if a slot ever fails to boot.

## Build changes

Both builds turn **app rollback off**. A slot you switch to would otherwise boot
as "pending verify". Muse only confirms itself once its control WebSocket
connects; after 300 seconds it marks itself invalid and reboots into the other
slot. The cost is no automatic fallback from a bad image, so a slot that won't
boot needs USB recovery.

**Muse firmware** — copy `partitions_dualboot.csv` into `esp32/` and append these
lines to `esp32/devices/sdkconfig.home-assistant-voice`. `tools/board.sh` has no
option for an extra overlay, and this file loads last:

```
CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions_dualboot.csv"
CONFIG_PARTITION_TABLE_OFFSET=0x8000
CONFIG_SECURE_SIGNED_APPS_NO_SECURE_BOOT=n
CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT=n
CONFIG_SECURE_BOOT_BUILD_SIGNED_BINARIES=n
CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=n
```

The signing lines are required. With signed-app checks on (the SDK default), Muse
rejects the unsigned ESPHome image and can't switch to it.

Delete `esp32/build-home-assistant-voice/` so these take effect, because an
existing `sdkconfig` overrides the defaults. Then build as usual with
`tools/board.sh home-assistant-voice build`.

**ESPHome firmware** — start from the official
`esphome/home-assistant-voice-pe` config, remove the `ota:` block, and add this
to its `esp32:` section. Keep the existing options, and quote every value, because
ESPHome rejects anything that isn't a string here:

```yaml
esp32:
  partitions: partitions_dualboot.csv   # same file, next to the YAML
  framework:
    type: esp-idf
    sdkconfig_options:
      CONFIG_PARTITION_TABLE_OFFSET: "0x8000"   # explicit so the two builds can't drift
      CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE: "n"
```

## First flash (USB, one time)

This **erases the whole ESP32 flash**, including existing Wi-Fi and Home
Assistant settings. Without it, old data can remain in `otadata` and `nvs`, since
`write_flash` only touches the regions it's given.

```bash
esptool.py --chip esp32s3 --port /dev/ttyACM0 erase_flash
```

```bash
esptool.py --chip esp32s3 --port /dev/ttyACM0 write_flash \
  0x0      bootloader.bin \
  0x8000   partition-table.bin \
  0x20000  muse-gadget.bin \
  0x720000 firmware.ota.bin
```

- **`firmware.ota.bin`** is ESPHome's app-only image. Don't use
  `firmware.factory.bin`: it bundles a bootloader and partition table, so it
  won't run from a slot.
- **`bootloader.bin`** comes from one build only. Start with ESPHome's, since it
  uses the older ESP-IDF — see the version risk below.
- **`partition-table.bin`** can come from either build, because the CSV and offset
  are the same.

## What you give up — read before building

- **No self-OTA on either side.** Each firmware's OTA updater writes to "the
  other slot" — which here is the *other assistant's home*. Leave the Muse
  device-OTA off (it already is for this board in the SDK's feature matrix)
  and **remove the `ota:` platform from the ESPHome build**. All updates are
  USB flashes to the slot offset (`write_flash 0x20000 …` or `0x720000 …`).
  A four-slot layout (A/B per firmware) could restore OTA, but both OTA
  implementations would need patching to stay in their own pair — v2
  territory.
- **One assistant at a time.** While you're in Muse mode, Home Assistant
  sees the satellite as unavailable, and vice versa. Switching is a reboot,
  not a handoff.
- **Wi-Fi is set up twice.** Both firmwares keep the IDF Wi-Fi driver's
  credentials in RAM (`WIFI_STORAGE_RAM`), so neither inherits the other's
  network. Set it up once on each side.
- **ESPHome's factory reset wipes both sides.** It erases the entire shared `nvs`
  partition, so Muse's saved settings go too.
- **Muse mode is push-to-talk** (that's how the SDK's Voice PE build works);
  wake word lives on the ESPHome side.
- **Main integration risk: bootloader/IDF version skew.** The Muse SDK builds with
  ESP-IDF 6.0.3, and ESPHome currently recommends 5.5.6. A single flashed
  bootloader has to boot both apps. Older bootloaders are generally expected to
  boot newer apps, but this exact pair is untested. If it misbehaves, rebuild one
  side against the other's IDF version.

## Test first

Stop at the first failure, since each step depends on the one before.

1. Flash the bootloader and table with **only Muse**, and confirm it boots from
   `ota_0` with the older bootloader.
2. Add ESPHome, then switch from each side and check that both directions work.
3. Power-cycle after each switch, and stay in Muse for over 5 minutes with no
   control connection. The selected slot should stick, which proves rollback is off.
4. Use both firmwares fully, and watch for NVS write errors in the logs. The
   24 KB `nvs` partition is shared, and its size hasn't been checked against real
   use. If it's too small, it can grow into the free space up to 0x20000 by
   moving the partitions after it.

## Files in this folder

- `partitions_dualboot.csv` — the shared partition table
- `ota_switch.h` — the boot-partition switch helper used by both sides
- `README.md` — this document
