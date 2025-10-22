"""Player management commands for Minecraft RCON."""

from typing import Optional
from .base import BaseCommand

class PlayerCommands(BaseCommand):
    def ban(self, player: str, reason: Optional[str] = None) -> str:
        """Ban a player from the server.
        
        Args:
            player: Player name or UUID
            reason: Optional ban reason
        """
        command = f"ban {player}"
        if reason:
            command += f" {reason}"
        return self.execute(command)

    def ban_ip(self, ip: str, reason: Optional[str] = None) -> str:
        """Ban an IP address from the server.
        
        Args:
            ip: IP address to ban
            reason: Optional ban reason
        """
        command = f"ban-ip {ip}"
        if reason:
            command += f" {reason}"
        return self.execute(command)

    def pardon(self, player: str) -> str:
        """Unban a player from the server.
        
        Args:
            player: Player name or UUID
        """
        return self.execute(f"pardon {player}")

    def pardon_ip(self, ip: str) -> str:
        """Unban an IP address from the server.
        
        Args:
            ip: IP address to unban
        """
        return self.execute(f"pardon-ip {ip}")

    def op(self, player: str) -> str:
        """Give operator status to a player.
        
        Args:
            player: Player name or UUID
        """
        return self.execute(f"op {player}")

    def deop(self, player: str) -> str:
        """Remove operator status from a player.
        
        Args:
            player: Player name or UUID
        """
        return self.execute(f"deop {player}")

    def kick(self, player: str, reason: Optional[str] = None) -> str:
        """Kick a player from the server.
        
        Args:
            player: Player name or UUID
            reason: Optional kick reason
        """
        command = f"kick {player}"
        if reason:
            command += f" {reason}"
        return self.execute(command)

    def whitelist_add(self, player: str) -> str:
        """Add a player to the whitelist.
        
        Args:
            player: Player name or UUID
        """
        return self.execute(f"whitelist add {player}")

    def whitelist_remove(self, player: str) -> str:
        """Remove a player from the whitelist.
        
        Args:
            player: Player name or UUID
        """
        return self.execute(f"whitelist remove {player}")

    def whitelist_list(self) -> str:
        """List all whitelisted players."""
        return self.execute("whitelist list")

    def whitelist_on(self) -> str:
        """Enable the whitelist."""
        return self.execute("whitelist on")

    def whitelist_off(self) -> str:
        """Disable the whitelist."""
        return self.execute("whitelist off")

    def whitelist_reload(self) -> str:
        """Reload the whitelist from file."""
        return self.execute("whitelist reload")

    def list_players(self, show_uuids: bool = False) -> str:
        """List all online players.
        
        Args:
            show_uuids: Whether to show player UUIDs
        """
        command = "list"
        if show_uuids:
            command += " uuids"
        return self.execute(command)

    def msg(self, target: str, message: str) -> str:
        """Send a private message to a player.
        
        Args:
            target: Player name or UUID
            message: Message to send
        """
        return self.execute(f"msg {target} {message}")

    def tell(self, target: str, message: str) -> str:
        """Alias for msg command."""
        return self.msg(target, message)

    def say(self, message: str) -> str:
        """Broadcast a message to all players.
        
        Args:
            message: Message to broadcast
        """
        return self.execute(f"say {message}")

    def me(self, action: str) -> str:
        """Display an action in chat.
        
        Args:
            action: Action to display
        """
        return self.execute(f"me {action}")