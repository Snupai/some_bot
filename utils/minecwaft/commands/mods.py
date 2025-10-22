"""Mod-specific commands for Minecraft RCON."""

from typing import Optional, Union, Literal
from .base import BaseCommand

class ModCommands(BaseCommand):
    # FTB Commands
    def ftb_ultimine(self) -> str:
        """Toggle FTB Ultimine."""
        return self.execute("ftbultimine")

    def ftb_gamemode(self, mode: Literal['survival', 'creative', 'adventure', 'spectator']) -> str:
        """Change gamemode using FTB Library.
        
        Args:
            mode: Game mode to set
        """
        return self.execute(f"ftblibrary gamemode {mode}")

    def ftb_chunks_claim(self) -> str:
        """Claim the current chunk."""
        return self.execute("ftbchunks claim")

    def ftb_chunks_unclaim(self) -> str:
        """Unclaim the current chunk."""
        return self.execute("ftbchunks unclaim")

    def ftb_quests_open(self) -> str:
        """Open FTB Quests book."""
        return self.execute("ftbquests open_book")

    # Create Mod Commands
    def create_train(self, command: str) -> str:
        """Execute Create mod train command.
        
        Args:
            command: Train subcommand
        """
        return self.execute(f"create train {command}")

    def create_scroll(self, spell: str, level: int) -> str:
        """Create a spell scroll.
        
        Args:
            spell: Spell name
            level: Spell level
        """
        return self.execute(f"createScroll {spell} {level}")

    # Mekanism Commands
    def mek_debug(self) -> str:
        """Toggle Mekanism debug mode."""
        return self.execute("mek debug")

    # Applied Energistics 2 Commands
    def ae2_chunklogger(self) -> str:
        """Toggle AE2 chunk logging."""
        return self.execute("ae2 chunklogger")

    # Sophisticated Backpacks Commands
    def sbp_list(self) -> str:
        """List sophisticated backpacks."""
        return self.execute("sbp list")

    # Curios Commands
    def curios_list(self, target: str) -> str:
        """List curios slots.
        
        Args:
            target: Target selector
        """
        return self.execute(f"curios list {target}")

    # Compact Machines Commands
    def compact_machines_tp(self, room: int) -> str:
        """Teleport to compact machine room.
        
        Args:
            room: Room number
        """
        return self.execute(f"compactmachines tp {room}")

    # KubeJS Commands
    def kubejs_reload(self) -> str:
        """Reload KubeJS scripts."""
        return self.execute("kubejs reload")

    def kubejs_errors(self) -> str:
        """Show KubeJS errors."""
        return self.execute("kubejs errors")

    # Supplementaries Commands
    def supplementaries_globe(self) -> str:
        """Open the globe GUI."""
        return self.execute("supplementaries globe")

    # Minecolonies Commands
    def minecolonies_colony_info(self, id: Optional[int] = None) -> str:
        """Show colony information.
        
        Args:
            id: Optional colony ID
        """
        command = "mc colony info"
        if id is not None:
            command += f" {id}"
        return self.execute(command)

    def minecolonies_citizens_info(self, id: Optional[int] = None) -> str:
        """Show citizens information.
        
        Args:
            id: Optional citizen ID
        """
        command = "mc citizens info"
        if id is not None:
            command += f" {id}"
        return self.execute(command)

    # Common utility mod commands
    def home(self, name: Optional[str] = None) -> str:
        """Teleport to home.
        
        Args:
            name: Optional home name
        """
        command = "home"
        if name:
            command += f" {name}"
        return self.execute(command)

    def sethome(self, name: Optional[str] = None) -> str:
        """Set home location.
        
        Args:
            name: Optional home name
        """
        command = "sethome"
        if name:
            command += f" {name}"
        return self.execute(command)

    def warp(self, name: str) -> str:
        """Teleport to warp point.
        
        Args:
            name: Warp name
        """
        return self.execute(f"warp {name}")

    def setwarp(self, name: str) -> str:
        """Set warp point.
        
        Args:
            name: Warp name
        """
        return self.execute(f"setwarp {name}")

    def tpa(self, target: str) -> str:
        """Request teleport to player.
        
        Args:
            target: Player name
        """
        return self.execute(f"tpa {target}")

    def back(self) -> str:
        """Teleport to previous location."""
        return self.execute("back")

    def spawn(self) -> str:
        """Teleport to spawn point."""
        return self.execute("spawn")