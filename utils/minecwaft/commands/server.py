"""Server management commands for Minecraft RCON."""

from typing import Optional
from .base import BaseCommand

class ServerCommands(BaseCommand):
    def stop(self) -> str:
        """Stop the server."""
        return self.execute("stop")

    def reload(self) -> str:
        """Reload server resources."""
        return self.execute("reload")

    def debug_start(self) -> str:
        """Start debug profiling."""
        return self.execute("debug start")

    def debug_stop(self) -> str:
        """Stop debug profiling."""
        return self.execute("debug stop")

    def debug_function(self, name: str) -> str:
        """Run debug function.
        
        Args:
            name: Function name
        """
        return self.execute(f"debug function {name}")

    def function(self, name: str, *args: str) -> str:
        """Run a function.
        
        Args:
            name: Function name
            args: Optional function arguments
        """
        command = f"function {name}"
        if args:
            command += f" {' '.join(args)}"
        return self.execute(command)

    def datapack_list(self) -> str:
        """List all datapacks."""
        return self.execute("datapack list")

    def datapack_enable(self, name: str) -> str:
        """Enable a datapack.
        
        Args:
            name: Datapack name
        """
        return self.execute(f"datapack enable {name}")

    def datapack_disable(self, name: str) -> str:
        """Disable a datapack.
        
        Args:
            name: Datapack name
        """
        return self.execute(f"datapack disable {name}")

    def banlist(self, type: Optional[str] = None) -> str:
        """Show banlist.
        
        Args:
            type: Optional type ('ips' or 'players')
        """
        command = "banlist"
        if type:
            command += f" {type}"
        return self.execute(command)

    def setidletimeout(self, minutes: int) -> str:
        """Set player idle timeout.
        
        Args:
            minutes: Timeout in minutes
        """
        return self.execute(f"setidletimeout {minutes}")

    def perf_start(self) -> str:
        """Start performance profiling."""
        return self.execute("perf start")

    def perf_stop(self) -> str:
        """Stop performance profiling."""
        return self.execute("perf stop")

    def jfr_start(self) -> str:
        """Start Java Flight Recorder."""
        return self.execute("jfr start")

    def jfr_stop(self) -> str:
        """Stop Java Flight Recorder."""
        return self.execute("jfr stop")

    def list_mods(self) -> str:
        """List all installed mods."""
        return self.execute("modlist")

    def statistics_entities(self) -> str:
        """Show entity statistics."""
        return self.execute("statistics entities")

    def statistics_block_entities(self) -> str:
        """Show block entity statistics."""
        return self.execute("statistics block-entities")

    def mobcaps(self) -> str:
        """Show mob spawn caps."""
        return self.execute("mobcaps")