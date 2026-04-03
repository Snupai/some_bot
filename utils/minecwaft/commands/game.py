"""Game mechanics commands for Minecraft RCON."""

from typing import Optional, Union, Literal
from .base import BaseCommand

class GameCommands(BaseCommand):
    def gamemode(self, mode: Literal['survival', 'creative', 'adventure', 'spectator'], 
                target: Optional[str] = None) -> str:
        """Change game mode for a player.
        
        Args:
            mode: Game mode to set
            target: Optional player target
        """
        command = f"gamemode {mode}{self._format_target(target)}"
        return self.execute(command)

    def gamerule(self, rule: str, value: Optional[Union[bool, int]] = None) -> str:
        """Get or set a game rule.
        
        Args:
            rule: Game rule name
            value: Optional value to set
        """
        command = f"gamerule {rule}"
        if value is not None:
            if isinstance(value, bool):
                command += f" {self._format_bool(value)}"
            else:
                command += f" {value}"
        return self.execute(command)

    def experience_add(self, target: str, amount: int, type: Literal['points', 'levels'] = 'points') -> str:
        """Add experience to a player.
        
        Args:
            target: Player target
            amount: Amount to add
            type: Experience type
        """
        return self.execute(f"experience add {target} {amount} {type}")

    def experience_set(self, target: str, amount: int, type: Literal['points', 'levels'] = 'points') -> str:
        """Set experience for a player.
        
        Args:
            target: Player target
            amount: Amount to set
            type: Experience type
        """
        return self.execute(f"experience set {target} {amount} {type}")

    def experience_query(self, target: str, type: Literal['points', 'levels']) -> str:
        """Query experience of a player.
        
        Args:
            target: Player target
            type: Experience type to query
        """
        return self.execute(f"experience query {target} {type}")

    def defaultgamemode(self, mode: Literal['survival', 'creative', 'adventure', 'spectator']) -> str:
        """Set the default game mode.
        
        Args:
            mode: Game mode to set as default
        """
        return self.execute(f"defaultgamemode {mode}")

    def spawnpoint(self, target: Optional[str] = None, 
                  x: Optional[float] = None,
                  y: Optional[float] = None,
                  z: Optional[float] = None) -> str:
        """Set spawn point for a player.
        
        Args:
            target: Optional player target
            x: Optional X coordinate
            y: Optional Y coordinate
            z: Optional Z coordinate
        """
        command = f"spawnpoint{self._format_target(target)}"
        if all(coord is not None for coord in (x, y, z)):
            command += f" {self._format_pos(x, y, z)}"
        return self.execute(command)

    def trigger(self, objective: str, mode: Literal['add', 'set'] = 'add', value: int = 1) -> str:
        """Trigger an objective.
        
        Args:
            objective: Objective to trigger
            mode: Trigger mode
            value: Value to add or set
        """
        return self.execute(f"trigger {objective} {mode} {value}")

    def scoreboard_objectives_add(self, name: str, criteria: str, display_name: Optional[str] = None) -> str:
        """Add a scoreboard objective.
        
        Args:
            name: Objective name
            criteria: Objective criteria
            display_name: Optional display name
        """
        command = f"scoreboard objectives add {name} {criteria}"
        if display_name:
            command += f" {display_name}"
        return self.execute(command)

    def scoreboard_objectives_remove(self, name: str) -> str:
        """Remove a scoreboard objective.
        
        Args:
            name: Objective name
        """
        return self.execute(f"scoreboard objectives remove {name}")

    def scoreboard_objectives_list(self) -> str:
        """List all scoreboard objectives."""
        return self.execute("scoreboard objectives list")

    def scoreboard_players_set(self, target: str, objective: str, score: int) -> str:
        """Set score for a player/target.
        
        Args:
            target: Target selector or player
            objective: Objective name
            score: Score to set
        """
        return self.execute(f"scoreboard players set {target} {objective} {score}")

    def scoreboard_players_add(self, target: str, objective: str, score: int) -> str:
        """Add to score for a player/target.
        
        Args:
            target: Target selector or player
            objective: Objective name
            score: Score to add
        """
        return self.execute(f"scoreboard players add {target} {objective} {score}")

    def scoreboard_players_remove(self, target: str, objective: str, score: int) -> str:
        """Remove from score for a player/target.
        
        Args:
            target: Target selector or player
            objective: Objective name
            score: Score to remove
        """
        return self.execute(f"scoreboard players remove {target} {objective} {score}")

    def scoreboard_players_reset(self, target: str, objective: Optional[str] = None) -> str:
        """Reset score(s) for a player/target.
        
        Args:
            target: Target selector or player
            objective: Optional objective name
        """
        command = f"scoreboard players reset {target}"
        if objective:
            command += f" {objective}"
        return self.execute(command)