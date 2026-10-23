"""Export this project's Claude Code sessions to ai-logs/, without secrets.

Usage: uv run python scripts/export_ai_logs.py

Claude Code keeps each session in ~/.claude/projects/<project-path>/*.jsonl. The script copies
them and, on the way, replaces:
- the value of every variable in .env (API key, workspace, HMAC key);
- anything shaped like a token (Anthropic, GitHub) or a workspace id;
- the author's email (git config user.email) and the home path (becomes ~), escaped or not.

It then checks that none of the replaced values is left in the exported files.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SESSIONS = Path.home() / ".claude" / "projects" / str(REPO).replace("/", "-")
OUT = REPO / "ai-logs" / "claude-code"

TOKEN_PATTERNS = {
    "ANTHROPIC_KEY": re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    "GITHUB_TOKEN": re.compile(r"\bgh[opsur]_[A-Za-z0-9]{10,}"),
    "WORKSPACE_ID": re.compile(r"\bwrkspc_[A-Za-z0-9]{6,}"),
}


def env_secrets() -> dict[str, str]:
    secrets = {}
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, value = line.split("=", 1)
                value = value.strip().strip("'\"")
                if len(value) >= 8 and not value.startswith("http"):
                    secrets[name.strip()] = value
    return secrets


def author_email() -> str | None:
    result = subprocess.run(
        ["git", "config", "user.email"], cwd=REPO, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or None


def scrub(text: str, literals: dict[str, str]) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for label, value in literals.items():
        n = text.count(value)
        if n:
            text = text.replace(value, f"[REMOVIDO:{label}]")
            counts[label] = counts.get(label, 0) + n
    for label, pattern in TOKEN_PATTERNS.items():
        text, n = pattern.subn(f"[REMOVIDO:{label}]", text)
        if n:
            counts[label] = counts.get(label, 0) + n
    return text, counts


def main() -> None:
    literals = {f"env:{k}": v for k, v in env_secrets().items()}
    if email := author_email():
        literals["EMAIL_AUTOR"] = email
        literals["EMAIL_AUTOR_USUARIO"] = email.split("@")[0]
    # the home path, also in its escaped forms (e.g. "\/Users\/me" inside a sed command)
    home = re.compile(r"(?:\\{1,2})?/".join(re.escape(p) for p in str(Path.home()).split("/")))

    OUT.mkdir(parents=True, exist_ok=True)
    sessions = sorted(SESSIONS.glob("*.jsonl"))
    if not sessions:
        raise SystemExit(f"Nenhuma sessão encontrada em {SESSIONS}")
    for src in sessions:
        text, counts = scrub(src.read_text(encoding="utf-8"), literals)
        text = home.sub("~", text)
        dest = OUT / src.name
        dest.write_text(text, encoding="utf-8")
        leftovers = [label for label, value in literals.items() if value in text]
        if leftovers:
            raise SystemExit(f"{dest.name}: ainda contém {leftovers}")
        print(f"{dest.relative_to(REPO)}: {src.stat().st_size // 1024} KB, substituições {counts}")


if __name__ == "__main__":
    main()
