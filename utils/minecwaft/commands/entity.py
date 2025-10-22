"""Entity management commands for Minecraft RCON."""

from typing import Optional, Union, Literal
from .base import BaseCommand

class EntityCommands(BaseCommand):
    def kill(self, target: Optional[str] = None) -> str:
        """Kill entities.
        
        Args:
            target: Optional target selector
        """
        command = "kill"
        if target:
            command += f" {target}"
        return self.execute(command)

    def summon(self, entity: str, x: Optional[float] = None, 
               y: Optional[float] = None, z: Optional[float] = None, 
               nbt: Optional[str] = None) -> str:
        """Summon an entity.
        
        Args:
            entity: Entity type to summon
            x: Optional X coordinate
            y: Optional Y coordinate
            z: Optional Z coordinate
            nbt: Optional NBT data
        """
        command = f"summon {entity}"
        if all(coord is not None for coord in (x, y, z)):
            command += f" {self._format_pos(x, y, z)}"
        if nbt:
            command += f" {nbt}"
        return self.execute(command)

    def tp(self, target: str, destination: Union[str, tuple[float, float, float]]) -> str:
        """Teleport entities.
        
        Args:
            target: Entity to teleport
            destination: Either target entity or coordinates (x, y, z)
        """
        command = f"tp {target}"
        if isinstance(destination, tuple):
            x, y, z = destination
            command += f" {self._format_pos(x, y, z)}"
        else:
            command += f" {destination}"
        return self.execute(command)

    def teleport(self, target: str, destination: Union[str, tuple[float, float, float]]) -> str:
        """Alias for tp command."""
        return self.tp(target, destination)

    def effect_give(self, target: str, effect: str, 
                   duration: Optional[int] = None,
                   amplifier: Optional[int] = None,
                   hide_particles: bool = False) -> str:
        """Give an effect to entities.
        
        Args:
            target: Target selector
            effect: Effect to give
            duration: Optional duration in seconds
            amplifier: Optional amplifier
            hide_particles: Whether to hide particles
        """
        command = f"effect give {target} {effect}"
        if duration is not None:
            command += f" {duration}"
            if amplifier is not None:
                command += f" {amplifier}"
                if hide_particles:
                    command += " true"
        return self.execute(command)

    def effect_clear(self, target: str, effect: Optional[str] = None) -> str:
        """Clear effects from entities.
        
        Args:
            target: Target selector
            effect: Optional specific effect to clear
        """
        command = f"effect clear {target}"
        if effect:
            command += f" {effect}"
        return self.execute(command)

    def attribute(self, target: str, attribute: str, 
                 operation: Literal['get', 'base', 'modifier']) -> str:
        """Get or modify entity attributes.
        
        Args:
            target: Target selector
            attribute: Attribute name
            operation: Operation to perform
        """
        return self.execute(f"attribute {target} {attribute} {operation}")

    def damage(self, target: str, amount: float, damage_type: Optional[str] = None) -> str:
        """Damage entities.
        
        Args:
            target: Target selector
            amount: Amount of damage
            damage_type: Optional damage type
        """
        command = f"damage {target} {amount}"
        if damage_type:
            command += f" {damage_type}"
        return self.execute(command)

    def ride(self, target: str, vehicle: Optional[str] = None) -> str:
        """Make entities ride or dismount.
        
        Args:
            target: Target selector
            vehicle: Optional vehicle target (None for dismount)
        """
        if vehicle:
            return self.execute(f"ride {target} mount {vehicle}")
        return self.execute(f"ride {target} dismount")

    def tag_add(self, target: str, tag: str) -> str:
        """Add a tag to entities.
        
        Args:
            target: Target selector
            tag: Tag to add
        """
        return self.execute(f"tag {target} add {tag}")

    def tag_remove(self, target: str, tag: str) -> str:
        """Remove a tag from entities.
        
        Args:
            target: Target selector
            tag: Tag to remove
        """
        return self.execute(f"tag {target} remove {tag}")

    def tag_list(self, target: str) -> str:
        """List tags on entities.
        
        Args:
            target: Target selector
        """
        return self.execute(f"tag {target} list")