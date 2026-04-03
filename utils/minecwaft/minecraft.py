"""Minecraft RCON interface with organized command categories.

This module provides a high-level interface for executing Minecraft RCON commands
through an organized and well-documented API. Commands are grouped into logical
categories like player management, world management, game mechanics, etc.

Example:
    ```python
    from utils.minecraft import Minecraft
    
    # Create a Minecraft instance
    mc = Minecraft("localhost", 25575, "password")
    
    # Use context manager to handle connections
    with mc as minecraft:
        # List all available commands
        minecraft.list_commands()
        
        # Use direct commands
        minecraft.help()
        minecraft.say("Hello everyone!")
        minecraft.list_online()  # List all online players
        
        # Or use organized categories
        minecraft.player.give("player1", "minecraft:diamond", 64)
        minecraft.world.time_set("day")
        minecraft.game.difficulty("hard")
    ```
"""

import inspect
from typing import Optional, Dict, List
from .minecraft_rcon import MinecraftRCON
from .commands.player import PlayerCommands
from .commands.world import WorldCommands
from .commands.game import GameCommands
from .commands.entity import EntityCommands
from .commands.block import BlockCommands
from .commands.inventory import InventoryCommands
from .commands.server import ServerCommands
from .commands.mods import ModCommands

class Minecraft:
    """Main interface for executing Minecraft RCON commands.
    
    This class provides access to all command categories through dedicated
    command handler instances. Each handler provides typed methods with
    proper documentation for their respective commands.
    
    Common commands like help() and say() are available directly on this class
    for convenience.
    
    Attributes:
        rcon (MinecraftRCON): The RCON connection handler
        player (PlayerCommands): Player management commands
        world (WorldCommands): World management commands
        game (GameCommands): Game mechanics commands
        entity (EntityCommands): Entity management commands
        block (BlockCommands): Block management commands
        inventory (InventoryCommands): Inventory management commands
        server (ServerCommands): Server management commands
        mods (ModCommands): Mod-specific commands
    """
    
    def __init__(self, ip: str, port: int, password: str):
        """Initialize Minecraft interface.
        
        Args:
            ip: Server IP address
            port: RCON port number
            password: RCON password
        """
        self.rcon = MinecraftRCON(ip, port, password)
        self.player = PlayerCommands(self.rcon)
        self.world = WorldCommands(self.rcon)
        self.game = GameCommands(self.rcon)
        self.entity = EntityCommands(self.rcon)
        self.block = BlockCommands(self.rcon)
        self.inventory = InventoryCommands(self.rcon)
        self.server = ServerCommands(self.rcon)
        self.mods = ModCommands(self.rcon)

    def __enter__(self):
        """Context manager entry that connects to RCON.
        
        Returns:
            Minecraft: The connected instance
        """
        self.rcon.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit that disconnects from RCON.
        
        Args:
            exc_type: Exception type if an error occurred
            exc_val: Exception value if an error occurred
            exc_tb: Exception traceback if an error occurred
        """
        self.rcon.disconnect()

    def connect(self):
        """Connect to the Minecraft server."""
        self.rcon.connect()

    def disconnect(self):
        """Disconnect from the Minecraft server."""
        self.rcon.disconnect()

    def command(self, cmd: str) -> str:
        """Execute a raw RCON command.
        
        This method allows executing raw commands directly if needed,
        but it's recommended to use the organized command categories instead.
        
        Args:
            cmd: The command to execute
            
        Returns:
            The command response from the server
        """
        return self.rcon.send_command(cmd)

    def list_commands(self) -> str:
        """List all available commands organized by category.
        
        Returns:
            A formatted string showing all available commands grouped by category
        """
        # Get direct commands from this class
        direct_commands = [name for name, func in inspect.getmembers(self, predicate=inspect.ismethod)
                         if not name.startswith('_') and name not in ['connect', 'disconnect', 'command', 'list_commands']]
        
        # Get commands from each category
        category_commands: Dict[str, List[str]] = {}
        for category_name, category in [
            ('player', self.player),
            ('world', self.world),
            ('game', self.game),
            ('entity', self.entity),
            ('block', self.block),
            ('inventory', self.inventory),
            ('server', self.server),
            ('mods', self.mods)
        ]:
            commands = [name for name, func in inspect.getmembers(category, predicate=inspect.ismethod)
                       if not name.startswith('_') and name != 'execute']
            if commands:
                category_commands[category_name] = commands
        
        # Format the output
        output = ["Available Minecraft RCON Commands:", ""]
        
        # Add direct commands
        output.append("Direct Commands (mc.command_name):")
        for cmd in sorted(direct_commands):
            output.append(f"  mc.{cmd}()")
        output.append("")
        
        # Add category commands
        output.append("Category Commands:")
        for category, commands in category_commands.items():
            output.append(f"\nmc.{category}:")
            for cmd in sorted(commands):
                output.append(f"  mc.{category}.{cmd}()")
        
        return "\n".join(output)

    def help(self, command: Optional[str] = None) -> str:
        """Get help about commands.
        
        Args:
            command: Optional specific command to get help for
            
        Returns:
            Help text from the server
        """
        cmd = "help"
        if command:
            cmd += f" {command}"
        return self.command(cmd)

    def say(self, message: str) -> str:
        """Broadcast a message to all players.
        
        Args:
            message: Message to broadcast
            
        Returns:
            Server response
        """
        return self.command(f"say {message}")

    def me(self, action: str) -> str:
        """Display an action in chat.
        
        Args:
            action: Action to display
            
        Returns:
            Server response
        """
        return self.command(f"me {action}")

    def tell(self, target: str, message: str) -> str:
        """Send a private message to a player.
        
        Args:
            target: Player name or target selector
            message: Message to send
            
        Returns:
            Server response
        """
        return self.command(f"tell {target} {message}")

    def msg(self, target: str, message: str) -> str:
        """Alias for tell command."""
        return self.tell(target, message)

    def list_online(self, show_uuids: bool = False) -> str:
        """List all online players.
        
        Args:
            show_uuids: Whether to show player UUIDs
            
        Returns:
            List of online players
        """
        cmd = "list"
        if show_uuids:
            cmd += " uuids"
        return self.command(cmd)