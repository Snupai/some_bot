"""Inventory management commands for Minecraft RCON."""

from typing import Optional, Union
from .base import BaseCommand

class InventoryCommands(BaseCommand):
    def give(self, target: str, item: str, count: Optional[int] = None) -> str:
        """Give items to players.
        
        Args:
            target: Target selector
            item: Item to give
            count: Optional item count
        """
        command = f"give {target} {item}"
        if count is not None:
            command += f" {count}"
        return self.execute(command)

    def clear(self, target: Optional[str] = None, 
             item: Optional[str] = None,
             max_count: Optional[int] = None) -> str:
        """Clear items from inventory.
        
        Args:
            target: Optional target selector
            item: Optional item to clear
            max_count: Optional maximum count to clear
        """
        command = "clear"
        if target:
            command += f" {target}"
            if item:
                command += f" {item}"
                if max_count is not None:
                    command += f" {max_count}"
        return self.execute(command)

    def item_replace(self, target: str, slot: str, 
                    item: str, count: Optional[int] = None) -> str:
        """Replace items in slots.
        
        Args:
            target: Target selector or block position
            slot: Slot to replace
            item: Replacement item
            count: Optional item count
        """
        command = f"item replace {target} {slot} with {item}"
        if count is not None:
            command += f" {count}"
        return self.execute(command)

    def item_modify(self, target: str, slot: str, modifier: str) -> str:
        """Modify items in slots.
        
        Args:
            target: Target selector or block position
            slot: Slot to modify
            modifier: Item modifier to apply
        """
        return self.execute(f"item modify {target} {slot} {modifier}")

    def enchant(self, target: str, enchantment: str, level: Optional[int] = None) -> str:
        """Enchant items.
        
        Args:
            target: Target selector
            enchantment: Enchantment to apply
            level: Optional enchantment level
        """
        command = f"enchant {target} {enchantment}"
        if level is not None:
            command += f" {level}"
        return self.execute(command)

    def loot_spawn(self, x: float, y: float, z: float, 
                  source: str, params: str) -> str:
        """Spawn loot in world.
        
        Args:
            x: X coordinate
            y: Y coordinate
            z: Z coordinate
            source: Loot source type
            params: Source parameters
        """
        return self.execute(f"loot spawn {self._format_pos(x, y, z)} {source} {params}")

    def loot_give(self, target: str, source: str, params: str) -> str:
        """Give loot to players.
        
        Args:
            target: Target selector
            source: Loot source type
            params: Source parameters
        """
        return self.execute(f"loot give {target} {source} {params}")

    def loot_insert(self, x: float, y: float, z: float,
                   source: str, params: str) -> str:
        """Insert loot into container.
        
        Args:
            x: X coordinate
            y: Y coordinate
            z: Z coordinate
            source: Loot source type
            params: Source parameters
        """
        return self.execute(f"loot insert {self._format_pos(x, y, z)} {source} {params}")

    def loot_replace(self, target: str, slot: str,
                    source: str, params: str,
                    count: Optional[int] = None) -> str:
        """Replace slot contents with loot.
        
        Args:
            target: Target selector or block position
            slot: Slot to replace
            source: Loot source type
            params: Source parameters
            count: Optional item count
        """
        command = f"loot replace {target} {slot} {source} {params}"
        if count is not None:
            command += f" {count}"
        return self.execute(command)