"""Base command handler for Minecraft RCON commands."""

from typing import Optional, Any
from ...minecwaft.minecraft_rcon import MinecraftRCON

class BaseCommand:
    def __init__(self, rcon: MinecraftRCON):
        self.rcon = rcon

    def execute(self, command: str) -> str:
        """Execute a raw command through RCON.
        
        Args:
            command: The command to execute
            
        Returns:
            The command response from the server
        """
        return self.rcon.send_command(command)

    def _format_target(self, target: Optional[str]) -> str:
        """Format target argument if provided.
        
        Args:
            target: Optional target selector or player name
            
        Returns:
            Formatted target string or empty string if None
        """
        return f" {target}" if target else ""

    def _format_pos(self, x: float, y: float, z: float) -> str:
        """Format position coordinates.
        
        Args:
            x: X coordinate
            y: Y coordinate 
            z: Z coordinate
            
        Returns:
            Formatted position string
        """
        return f"{x} {y} {z}"

    def _format_bool(self, value: bool) -> str:
        """Format boolean value for commands.
        
        Args:
            value: Boolean value to format
            
        Returns:
            'true' or 'false' string
        """
        return str(value).lower()