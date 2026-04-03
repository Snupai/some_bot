"""Block management commands for Minecraft RCON."""

from typing import Optional, Literal, Union
from .base import BaseCommand

class BlockCommands(BaseCommand):
    def setblock(self, x: float, y: float, z: float, block: str, 
                mode: Literal['destroy', 'keep', 'replace'] = 'replace') -> str:
        """Set a block at position.
        
        Args:
            x: X coordinate
            y: Y coordinate
            z: Z coordinate
            block: Block to set
            mode: Placement mode
        """
        return self.execute(f"setblock {self._format_pos(x, y, z)} {block} {mode}")

    def fill(self, x1: float, y1: float, z1: float,
             x2: float, y2: float, z2: float,
             block: str,
             mode: Literal['replace', 'keep', 'outline', 'hollow', 'destroy'] = 'replace',
             replace_block: Optional[str] = None) -> str:
        """Fill a region with blocks.
        
        Args:
            x1, y1, z1: Starting position
            x2, y2, z2: Ending position
            block: Block to fill with
            mode: Fill mode
            replace_block: Optional block to replace
        """
        command = f"fill {self._format_pos(x1, y1, z1)} {self._format_pos(x2, y2, z2)} {block} {mode}"
        if replace_block and mode == 'replace':
            command += f" {replace_block}"
        return self.execute(command)

    def clone(self, x1: float, y1: float, z1: float,
              x2: float, y2: float, z2: float,
              x: float, y: float, z: float,
              mode: Literal['normal', 'force', 'move'] = 'normal',
              mask_mode: Literal['replace', 'masked', 'filtered'] = 'replace',
              filter_block: Optional[str] = None) -> str:
        """Clone a region of blocks.
        
        Args:
            x1, y1, z1: Source starting position
            x2, y2, z2: Source ending position
            x, y, z: Destination position
            mode: Clone mode
            mask_mode: Masking mode
            filter_block: Block to filter (for filtered mode)
        """
        command = (f"clone {self._format_pos(x1, y1, z1)} {self._format_pos(x2, y2, z2)} "
                  f"{self._format_pos(x, y, z)} {mode} {mask_mode}")
        if filter_block and mask_mode == 'filtered':
            command += f" {filter_block}"
        return self.execute(command)

    def fillbiome(self, x1: float, y1: float, z1: float,
                 x2: float, y2: float, z2: float,
                 biome: str,
                 replace: bool = False) -> str:
        """Fill a region with a biome.
        
        Args:
            x1, y1, z1: Starting position
            x2, y2, z2: Ending position
            biome: Biome to set
            replace: Whether to replace only
        """
        command = f"fillbiome {self._format_pos(x1, y1, z1)} {self._format_pos(x2, y2, z2)} {biome}"
        if replace:
            command += " replace"
        return self.execute(command)

    def forceload_add(self, x1: float, z1: float, 
                     x2: Optional[float] = None, 
                     z2: Optional[float] = None) -> str:
        """Force chunks to stay loaded.
        
        Args:
            x1, z1: Chunk position or start of range
            x2, z2: Optional end of range
        """
        command = f"forceload add {x1} {z1}"
        if x2 is not None and z2 is not None:
            command += f" {x2} {z2}"
        return self.execute(command)

    def forceload_remove(self, x1: float, z1: float,
                        x2: Optional[float] = None,
                        z2: Optional[float] = None) -> str:
        """Allow chunks to unload.
        
        Args:
            x1, z1: Chunk position or start of range
            x2, z2: Optional end of range
        """
        command = f"forceload remove {x1} {z1}"
        if x2 is not None and z2 is not None:
            command += f" {x2} {z2}"
        return self.execute(command)

    def forceload_remove_all(self) -> str:
        """Allow all chunks to unload."""
        return self.execute("forceload remove all")

    def forceload_query(self, x: Optional[float] = None, z: Optional[float] = None) -> str:
        """Query forced chunks.
        
        Args:
            x, z: Optional chunk coordinates to query
        """
        command = "forceload query"
        if x is not None and z is not None:
            command += f" {x} {z}"
        return self.execute(command)