"""Inventory manager integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from slugify import slugify

from .const import UNIQUE_ID

import homeassistant.helpers.config_validation as cv
from homeassistant.const import Platform
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import (
    CONF_ITEM_ID,
    CONF_ITEM_VENDOR,
    DOMAIN,
)
from .coordinator import InventoryManagerItem
from .data import (
    InventoryManagerConfigEntry,
    InventoryManagerData,
)
from .services import async_setup_services

if TYPE_CHECKING:
    from homeassistant import core


_LOGGER = logging.getLogger(__name__)


PLATFORMS: list[str] = [
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: core.HomeAssistant, _config: dict) -> bool:
    """Register the actions, independent of the config entries."""
    async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: core.HomeAssistant, entry: InventoryManagerConfigEntry
) -> bool:
    """Set up platform from a ConfigEntry."""
    # All changes are pushed to the entities, so there is nothing to poll
    coordinator = InventoryManagerItem(
        entry, hass, logger=_LOGGER, name=DOMAIN, update_interval=None
    )
    entry.runtime_data = InventoryManagerData(
        device_info=DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=entry.data.get(CONF_ITEM_VENDOR),
            entry_type=DeviceEntryType.SERVICE,
            name=entry.title,
        ),
        coordinator=coordinator,
    )
    await _async_migrate_fork_unique_ids(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener to handle option changes
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_migrate_entry(
    hass: core.HomeAssistant, entry: InventoryManagerConfigEntry
) -> bool:
    """Migrate old config entries."""
    if entry.version > 1:
        return False

    if entry.minor_version < 2:  # noqa: PLR2004
        # Unique ids used to be derived from the title, so keep using it to
        # leave the existing entities (and their restored state) untouched.
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_ITEM_ID: entry.title},
            minor_version=2,
        )
        _LOGGER.debug("Migrated %s to version 1.2", entry.title)

    return True


async def async_unload_entry(
    hass: core.HomeAssistant, entry: InventoryManagerConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(
    hass: core.HomeAssistant, entry: InventoryManagerConfigEntry
) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_migrate_fork_unique_ids(
    hass: core.HomeAssistant,
    entry: InventoryManagerConfigEntry,
    coordinator: InventoryManagerItem,
) -> None:
    """
    One-time: move unique ids from my old fork's scheme to the item id scheme.

    Keeps entity ids, restored values and history. Remove once prod has run it.
    """
    old_to_new = {
        slugify(entry.entry_id + "_" + entity_type.name): config[UNIQUE_ID]
        for entity_type, config in coordinator.entity_config.items()
    }
    registry = er.async_get(hass)

    @callback
    def _migrate(entity_entry: er.RegistryEntry) -> dict[str, Any] | None:
        new_id = old_to_new.get(entity_entry.unique_id)
        if new_id is None:
            return None
        if registry.async_get_entity_id(entity_entry.domain, DOMAIN, new_id):
            _LOGGER.warning(
                "Not migrating %s: unique ID %s is already used",
                entity_entry.entity_id,
                new_id,
            )
            return None
        _LOGGER.info(
            "Migrating %s from unique ID %s to %s",
            entity_entry.entity_id,
            entity_entry.unique_id,
            new_id,
        )
        return {"new_unique_id": new_id}

    await er.async_migrate_entries(hass, entry.entry_id, _migrate)
