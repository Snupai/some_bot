"""World management commands for Minecraft RCON."""

from typing import Optional, Union, Literal
from .base import BaseCommand

class WorldCommands(BaseCommand):
    def weather(self, type: Literal['clear', 'rain', 'thunder']) -> str:
        """Change the weather.
        
        Args:
            type: Weather type to set
        """
        return self.execute(f"weather {type}")

    def time_set(self, time: Union[int, Literal['day', 'night', 'noon', 'midnight']]) -> str:
        """Set the world time.
        
        Args:
            time: Time to set (tick number or keyword)
        """
        return self.execute(f"time set {time}")

    def time_add(self, amount: int) -> str:
        """Add to the world time.
        
        Args:
            amount: Number of ticks to add
        """
        return self.execute(f"time add {amount}")

    def worldborder_set(self, diameter: float, time: Optional[int] = None) -> str:
        """Set the world border diameter.
        
        Args:
            diameter: New border diameter
            time: Optional time in seconds to take
        """
        command = f"worldborder set {diameter}"
        if time is not None:
            command += f" {time}"
        return self.execute(command)

    def worldborder_center(self, x: float, z: float) -> str:
        """Set the world border center.
        
        Args:
            x: X coordinate
            z: Z coordinate
        """
        return self.execute(f"worldborder center {x} {z}")

    def worldborder_damage_amount(self, damage: float) -> str:
        """Set damage per block beyond border.
        
        Args:
            damage: Damage amount per block
        """
        return self.execute(f"worldborder damage amount {damage}")

    def worldborder_damage_buffer(self, distance: float) -> str:
        """Set the border damage buffer.
        
        Args:
            distance: Distance in blocks
        """
        return self.execute(f"worldborder damage buffer {distance}")

    def worldborder_warning_distance(self, distance: int) -> str:
        """Set the warning distance.
        
        Args:
            distance: Distance in blocks
        """
        return self.execute(f"worldborder warning distance {distance}")

    def worldborder_warning_time(self, time: int) -> str:
        """Set the warning time.
        
        Args:
            time: Time in seconds
        """
        return self.execute(f"worldborder warning time {time}")

    def seed(self) -> str:
        """Get the world seed."""
        return self.execute("seed")

    def difficulty(self, level: Literal['peaceful', 'easy', 'normal', 'hard']) -> str:
        """Set the game difficulty.
        
        Args:
            level: Difficulty level
        """
        return self.execute(f"difficulty {level}")

    def save_all(self, flush: bool = False) -> str:
        """Save the world to disk.
        
        Args:
            flush: Whether to flush writes to disk
        """
        command = "save-all"
        if flush:
            command += " flush"
        return self.execute(command)

    def save_on(self) -> str:
        """Enable automatic saving."""
        return self.execute("save-on")

    def save_off(self) -> str:
        """Disable automatic saving."""
        return self.execute("save-off")

    def setworldspawn(self, x: Optional[float] = None, y: Optional[float] = None, z: Optional[float] = None) -> str:
        """Set the world spawn point.
        
        Args:
            x: Optional X coordinate
            y: Optional Y coordinate
            z: Optional Z coordinate
        """
        if all(coord is not None for coord in (x, y, z)):
            return self.execute(f"setworldspawn {self._format_pos(x, y, z)}")
        return self.execute("setworldspawn")