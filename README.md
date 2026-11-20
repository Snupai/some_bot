## Setup

1. Install Python from [here](https://www.python.org/downloads/)
2. Install Git from [here](https://git-scm.com/downloads)
3. Install VS Code from [here](https://code.visualstudio.com/download)
4. Open a terminal and clone the repo by running `git clone https://github.com/Snupai/Glyph_Bot.git`
5. CD into the cloned directory by running `cd Glyph_Bot`
6. Create a virtual environment by running `python -m venv .venv`
7. Activate the virtual environment by running `.\.venv\Scripts\activate` (windows) or `source .venv/bin/activate` (linux/macOS)
8. Install the requirements by running `pip install -r requirements.txt`
9. Copy `.env.example` to `.env` and fill in the values
10. Run the bot by running `python bot.py`

### Using uv (recommended)

```bash
uv sync            # installs everything from pyproject.toml / uv.lock (incl. dev tools)
uv run python bot.py
uv run pytest      # test suite
```

## Nyanjou – GPT-Live voice assistant with T3 Code

`/join` brings **Nyanjou** into your voice channel: a live voice assistant built on OpenAI **GPT-Live 1.1**. For
development work it hands tasks to **T3 Code** over MCP and keeps talking with you while T3 works. Only authorized
users (`/usr-mgmt`) can start tasks, and only when they are clearly identified as the speaker.

Requires Node.js ≥ 22.12 for the voice bridge (`cd bridge && npm ci`), an OpenAI API key, and a one-time T3 sign-in
(`uv run python tools/nyanjou_t3_login.py`).

Full setup, the security model, Ubuntu/systemd deployment and the test coverage are in
[docs/nyanjou-integration.md](docs/nyanjou-integration.md).

Commands: `/join` `/leave` `/status` `/tasks` `/cancel` `/voice` `/model` `/t3task`
