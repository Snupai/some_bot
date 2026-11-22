"""Nyanjou voice assistant services.

Modules:
    config          – environment-driven configuration
    authz           – Discord user authorization (allowed_users.sqlite + owner)
    audio           – PCM conversion, resampling, voice activity detection
    speaker_gate    – floor control and speaker attribution timeline
    discord_audio   – Pycord VoiceProtocol + client for the Node voice bridge
    live_client     – OpenAI GPT-Live WebSocket client
    t3_oauth        – OAuth token storage / login helpers for T3 Code MCP
    t3_mcp_client   – T3 Code MCP client
    task_store      – SQLite persistence of Nyanjou tasks
    task_manager    – async scheduling and monitoring of T3 Code tasks
    tools           – function tools exposed to the GPT-Live backend
    voice_session   – per-guild orchestration of bridge, Live and tools
    runtime         – shared runtime object attached to the bot
"""
