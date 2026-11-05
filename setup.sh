#!/usr/bin/env bash
# Set up the Voice PE dual-boot: get the sources, patch them, build both
# firmwares, and flash the device. See README.md.
#
# Untested on a real device. The flash stage erases the whole ESP32 flash, and
# only runs after you type a confirmation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$(dirname "$SCRIPT_DIR")"
IDF_DIR="${IDF_PATH:-$HOME/esp/esp-idf-v6}"
IDF_VERSION="v6.0.3"
PORT=""
MUSE_BOARD="home-assistant-voice"
MUSE_URL="https://github.com/facebookincubator/muse-gadget-sdk.git"
ESPHOME_URL="https://github.com/esphome/home-assistant-voice-pe.git"
MAX_SLOT_BYTES=$((0x700000))

usage() {
  cat <<EOF
Usage: ./setup.sh [STAGE] [--port PORT] [--workdir DIR]

Stages:
  all       prepare, build, then flash (default)
  prepare   clone the Muse and ESPHome sources and patch them
  build     build both firmwares
  flash     erase the device and flash both firmwares

Options:
  --port PORT     serial port, such as /dev/ttyACM0 (detected if there's only one)
  --workdir DIR   where the source clones go (default: this repo's parent folder)
  -h, --help      show this help

Environment:
  IDF_PATH        ESP-IDF $IDF_VERSION folder (default: ~/esp/esp-idf-v6)
  WIFI_SSID, WIFI_PASSWORD
                  used instead of asking, the first time secrets.yaml is created
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

say() {
  printf '\n==> %s\n' "$*"
}

find_python() {
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
      echo "$candidate"
      return
    fi
  done
  die "Python 3.8 or newer is required"
}

require_tool() {
  command -v "$1" >/dev/null 2>&1 || die "$1 isn't installed. $2"
}

require_unix() {
  case "$(uname -s)" in
    Darwin | Linux) ;;
    *) die "this stage needs macOS or Linux, which the Muse SDK requires" ;;
  esac
}

clone_if_missing() {
  local url="$1" dest="$2"
  if [ -d "$dest/.git" ]; then
    echo "  using existing clone: $dest"
    return
  fi
  [ ! -e "$dest" ] || die "$dest exists but isn't a git clone"
  git clone --depth 1 "$url" "$dest"
}

stage_prepare() {
  say "Getting the sources"
  require_tool git "Install git, then try again."
  clone_if_missing "$MUSE_URL" "$MUSE_DIR"
  clone_if_missing "$ESPHOME_URL" "$ESPHOME_DIR"

  local ssid="${WIFI_SSID:-}" password="${WIFI_PASSWORD:-}"
  if [ ! -e "$ESPHOME_DIR/secrets.yaml" ] && [ -z "$ssid" ]; then
    say "Wi-Fi details for the ESPHome firmware"
    read -r -p "Network name: " ssid || die "no network name given"
    read -r -s -p "Password (hidden): " password || die "no password given"
    echo
    [ -n "$ssid" ] || die "no network name given"
  fi
  if [ ! -e "$ESPHOME_DIR/secrets.yaml" ] && [ -z "$password" ]; then
    echo "warning: no Wi-Fi password given, so this only works on an open network" >&2
  fi

  say "Patching the sources"
  local python
  python="$(find_python)"
  WIFI_SSID="$ssid" WIFI_PASSWORD="$password" "$python" \
    "$SCRIPT_DIR/scripts/patch_sources.py" \
    --shared "$SCRIPT_DIR" --muse "$MUSE_DIR" --esphome "$ESPHOME_DIR"
}

load_idf() {
  [ -f "$IDF_DIR/export.sh" ] || die "ESP-IDF $IDF_VERSION not found at $IDF_DIR. Set IDF_PATH, or see README.md."
  # export.sh isn't written for strict mode. Only call this inside a subshell.
  set +eu
  . "$IDF_DIR/export.sh" >/dev/null
  local status=$?
  set -eu
  [ "$status" -eq 0 ] || die "couldn't load ESP-IDF from $IDF_DIR"
}

build_muse() {
  say "Building Muse"
  (
    load_idf
    case "$(idf.py --version)" in
      *"$IDF_VERSION"*) ;;
      *) die "the Muse SDK needs ESP-IDF $IDF_VERSION, but found: $(idf.py --version)" ;;
    esac
    cd "$MUSE_DIR/esp32"
    # An existing sdkconfig would override the new settings.
    rm -rf "build-$MUSE_BOARD"
    tools/board.sh "$MUSE_BOARD" build
  )
}

build_esphome() {
  say "Building ESPHome"
  require_tool esphome "Run: pip install esphome"
  # Runs outside the ESP-IDF environment, because ESPHome uses its own toolchain.
  (
    cd "$ESPHOME_DIR"
    esphome compile home-assistant-voice.yaml
  )
}

stage_build() {
  require_unix
  [ -f "$ESPHOME_DIR/secrets.yaml" ] || die "sources aren't prepared. Run: ./setup.sh prepare"
  build_muse
  build_esphome
}

# Print the most recently built file with this name in the Voice PE build folder.
newest_esphome_file() {
  # A missing build folder isn't fatal here: the caller reports the missing file.
  find "$ESPHOME_DIR/.esphome/build/home-assistant-voice" -type f -name "$1" -exec ls -t {} + 2>/dev/null | head -n 1 || true
}

# Refuse a build that was made before its sources were patched.
require_built_after() {
  [ "$1" -nt "$2" ] || die "$1 is older than $2. Run: ./setup.sh build"
}

detect_port() {
  local found=() candidate
  for candidate in /dev/ttyACM* /dev/ttyUSB* /dev/cu.usbmodem* /dev/cu.usbserial*; do
    if [ -e "$candidate" ]; then
      found+=("$candidate")
    fi
  done
  [ "${#found[@]}" -eq 1 ] || die "found ${#found[@]} serial ports. Choose one with --port."
  echo "${found[0]}"
}

require_file() {
  [ -f "$1" ] || die "$2 not found${1:+: $1}. Run: ./setup.sh build"
}

require_fits_slot() {
  local size
  size="$(wc -c <"$1")"
  [ "$size" -le "$MAX_SLOT_BYTES" ] || die "$1 is too big for its slot ($size bytes)"
}

stage_flash() {
  require_unix
  local muse_build="$MUSE_DIR/esp32/build-$MUSE_BOARD"
  local table="$muse_build/partition_table/partition-table.bin"
  local muse_app="$muse_build/muse-gadget.bin"
  local bootloader esphome_app
  bootloader="$(newest_esphome_file bootloader.bin)"
  esphome_app="$(newest_esphome_file firmware.ota.bin)"

  require_file "$bootloader" "ESPHome bootloader"
  require_file "$table" "partition table"
  require_file "$muse_app" "Muse firmware"
  require_file "$esphome_app" "ESPHome firmware"
  require_fits_slot "$muse_app"
  require_fits_slot "$esphome_app"
  require_built_after "$muse_app" "$MUSE_DIR/esp32/main/app.c"
  require_built_after "$esphome_app" "$ESPHOME_DIR/home-assistant-voice.yaml"

  [ -n "$PORT" ] || PORT="$(detect_port)"

  say "About to flash the device on $PORT"
  cat <<EOF
This ERASES THE WHOLE ESP32 FLASH, including its Wi-Fi and Home Assistant settings.

  0x0       $bootloader
  0x8000    $table
  0x20000   $muse_app
  0x720000  $esphome_app

EOF
  local answer
  read -r -p "Type ERASE to continue: " answer || die "not confirmed, so nothing was changed"
  [ "$answer" = "ERASE" ] || die "not confirmed, so nothing was changed"

  (
    load_idf
    # One command, so a USB reset between erasing and writing can't leave the
    # device blank.
    python -m esptool --chip esp32s3 --port "$PORT" write_flash --erase-all \
      0x0 "$bootloader" \
      0x8000 "$table" \
      0x20000 "$muse_app" \
      0x720000 "$esphome_app"
  )

  say "Flashed. The device now starts in Muse."
  cat <<EOF
Next, follow steps 5 to 7 of the README's Setup section:
  - Send your SDK token: cd "$MUSE_DIR/esp32" && tools/board.sh $MUSE_BOARD monitor "$PORT"
  - Add the device in the Muse app.
  - Double-press the centre button to switch to ESPHome, then add it to Home Assistant.
EOF
}

main() {
  local stage="all"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      all | prepare | build | flash) stage="$1" ;;
      --port)
        [ "$#" -ge 2 ] || die "--port needs a value"
        PORT="$2"
        shift
        ;;
      --workdir)
        [ "$#" -ge 2 ] || die "--workdir needs a value"
        WORK_DIR="$2"
        shift
        ;;
      -h | --help)
        usage
        return
        ;;
      *)
        usage >&2
        die "unknown option: $1"
        ;;
    esac
    shift
  done

  mkdir -p "$WORK_DIR"
  WORK_DIR="$(cd "$WORK_DIR" && pwd)"
  MUSE_DIR="$WORK_DIR/muse-gadget-sdk"
  ESPHOME_DIR="$WORK_DIR/home-assistant-voice-pe"
  case "$WORK_DIR" in
    *" "*) echo "warning: firmware builds can fail in a path with spaces. Consider --workdir." >&2 ;;
  esac

  case "$stage" in
    prepare) stage_prepare ;;
    build) stage_build ;;
    flash) stage_flash ;;
    all)
      # Fail before prompting or building if this machine can't finish the job.
      require_unix
      stage_prepare
      stage_build
      stage_flash
      ;;
  esac
}

main "$@"
