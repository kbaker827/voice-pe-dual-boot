// ota_switch.h — shared "switch assistant" helper for the Voice PE dual-boot.
//
// Add this file next to your ESPHome YAML and list it under `esphome: includes:`,
// or include it from the Muse gadget firmware (e.g. a gesture handled by
// main/button.c). It flips the ESP-IDF boot partition recorded in otadata and
// reboots; the bootloader then starts the other firmware.
//
// Requires the dual-boot partition table (partitions_dualboot.csv):
//   ota_0 = Muse gadget firmware, ota_1 = ESPHome firmware.
//
// esp_ota_set_boot_partition() validates the target image. If the running build
// has signed-app checks on, it rejects an image its key did not sign, so the Muse
// build must turn signing off (see README.md). It sets the target as "pending
// verify" if the running build has rollback on, so rollback must be off too.

#pragma once

#include "esp_err.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_system.h"

#define DUALBOOT_LOG_TAG "dualboot"

// subtype: ESP_PARTITION_SUBTYPE_APP_OTA_0 (boot Muse) or
//          ESP_PARTITION_SUBTYPE_APP_OTA_1 (boot ESPHome)
//
// Does not return on success, because the device reboots. Otherwise returns why
// the switch did not happen, and the current firmware keeps running.
static inline esp_err_t dualboot_switch_to(esp_partition_subtype_t subtype) {
    const esp_partition_t *target =
        esp_partition_find_first(ESP_PARTITION_TYPE_APP, subtype, NULL);
    if (target == NULL) {
        ESP_LOGE(DUALBOOT_LOG_TAG, "slot not in partition table");
        return ESP_ERR_NOT_FOUND;
    }
    if (esp_ota_get_boot_partition() == target) {
        ESP_LOGI(DUALBOOT_LOG_TAG, "%s is already selected", target->label);
        return ESP_OK;
    }
    esp_err_t err = esp_ota_set_boot_partition(target);
    if (err != ESP_OK) {
        // ESP_ERR_OTA_VALIDATE_FAILED: the slot is empty, corrupt, or unsigned
        // while this build has signed-app checks on.
        ESP_LOGE(DUALBOOT_LOG_TAG, "cannot select %s: %s", target->label,
                 esp_err_to_name(err));
        return err;
    }
    esp_restart();
    return ESP_OK;  // not reached
}

static inline esp_err_t dualboot_switch_to_muse(void) {
    return dualboot_switch_to(ESP_PARTITION_SUBTYPE_APP_OTA_0);
}

static inline esp_err_t dualboot_switch_to_esphome(void) {
    return dualboot_switch_to(ESP_PARTITION_SUBTYPE_APP_OTA_1);
}
