from mcrcon import MCRcon
from mcstatus import JavaServer
import json
from typing import List, Optional, Dict, Any, Tuple
import time
import socket
import os

class MinecraftRCON:
    def __init__(self, ip: str, port: int, password: str, max_retries: int = 3):
        """
        Initialize MinecraftRCON with server details.
        
        Args:
            ip (str): Server IP address
            port (int): RCON port
            password (str): RCON password
            max_retries (int): Maximum number of connection retries
        """
        self.ip = ip
        self.port = port
        self.password = password
        self.max_retries = max_retries
        self.client: Optional[MCRcon] = None

    def connect(self) -> bool:
        """
        Establish connection to the server with retries.
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        for attempt in range(self.max_retries):
            try:
                if self.client:
                    try:
                        self.client.disconnect()
                    except:
                        pass
                
                # First check if port is open
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex((self.ip, self.port))
                sock.close()
                
                if result != 0:
                    print(f"Port {self.port} is not open on {self.ip}")
                    return False

                self.client = MCRcon(self.ip, self.password, self.port, timeout=5)
                self.client.connect()
                return True
            except ConnectionRefusedError:
                print(f"Connection refused on attempt {attempt + 1}. Server might not be running or RCON is not enabled.")
            except socket.timeout:
                print(f"Connection timed out on attempt {attempt + 1}. Server might be too slow to respond.")
            except Exception as e:
                print(f"Connection attempt {attempt + 1} failed: {str(e)}")
            
            if self.client:
                try:
                    self.client.disconnect()
                except:
                    pass
                self.client = None
            
            if attempt < self.max_retries - 1:
                time.sleep(1)  # Wait before retrying
        
        return False

    def disconnect(self):
        """Close the RCON connection if it exists."""
        if self.client:
            try:
                self.client.disconnect()
            except:
                pass
            finally:
                self.client = None

    def send_command(self, command: str) -> str:
        """
        Send a command to the server with reconnection handling.
        
        Args:
            command (str): Command to send
            
        Returns:
            str: Server response
        """
        try:
            if not self.client and not self.connect():
                return "Error: Could not establish connection to server"
            
            try:
                response = self.client.command(command)
                return response if response else "Command executed successfully (no response)"
            except Exception as e:
                # If command fails, try reconnecting once
                print(f"Command failed: {str(e)}, attempting reconnection...")
                if self.connect():
                    try:
                        response = self.client.command(command)
                        return response if response else "Command executed successfully (no response)"
                    except Exception as e:
                        return f"Error executing command after reconnection: {str(e)}"
                return f"Error executing command: {str(e)}"
        except Exception as e:
            return f"Error: {str(e)}"
        finally:
            # Always disconnect after command to ensure clean state
            self.disconnect()

    def _read_json_file(self, filename: str) -> List[Dict[str, Any]]:
        """
        Read and parse a JSON file from the current working directory.
        
        Args:
            filename (str): Name of the JSON file to read
            
        Returns:
            List[Dict[str, Any]]: Parsed JSON content or empty list if file doesn't exist
        """
        try:
            file_path = os.path.join(os.getcwd(), filename)
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error reading {filename}: {str(e)}")
        return []

    def get_whitelist(self) -> List[Dict[str, Any]]:
        """
        Get the server's whitelist from whitelist.json.
        
        Returns:
            List[Dict[str, Any]]: List of whitelisted players with their details
        """
        return self._read_json_file('whitelist.json')

    def add_to_whitelist(self, player_name: str) -> Tuple[bool, str]:
        """
        Add a player to the whitelist using RCON command.
        
        Args:
            player_name (str): Name of the player to whitelist
            
        Returns:
            Tuple[bool, str]: (Success status, Message)
        """
        try:
            response = self.send_command(f"whitelist add {player_name}")
            if "Added" in response:
                return True, f"Successfully added {player_name} to whitelist"
            elif "already whitelisted" in response:
                return False, f"Player {player_name} is already whitelisted"
            else:
                return False, f"Failed to add player: {response}"
        except Exception as e:
            return False, f"Error adding to whitelist: {str(e)}"

    def get_ops(self) -> List[Dict[str, Any]]:
        """
        Get the server's operator list from ops.json.
        
        Returns:
            List[Dict[str, Any]]: List of server operators with their details
        """
        return self._read_json_file('ops.json')

    def get_server_status(self) -> Dict[str, Any]:
        """
        Get the server's status including player count, version, etc.
        
        Returns:
            Dict[str, Any]: Server status information
        """
        try:
            # First check if port is open
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((self.ip, self.port))
            sock.close()
            
            if result != 0:
                return {
                    "online": False,
                    "error": f"Port {self.port} is not open on {self.ip}"
                }

            # Use mcstatus to query the server
            server = JavaServer(self.ip)
            status = server.status()
            
            return {
                "online": True,
                "version": status.version.name,
                "protocol": status.version.protocol,
                "players_online": status.players.online,
                "players_max": status.players.max,
                "latency": status.latency,
                "description": status.description
            }
        except Exception as e:
            return {
                "online": False,
                "error": str(e)
            }