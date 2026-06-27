"""
On-demand ARCHITECTURE.md updater and discrepancy checker.

Reads the full git log and current codebase, then asks Claude to:
  1. Update ARCHITECTURE.md to reflect any changes since the last sync
  2. Flag any discrepancies between what ARCHITECTURE.md says and what's in the code

Run this at the end of a build session — after you're done committing for the day:
  python update_docs.py

You control when tokens are spent. One run per session is typical (~$0.50-1.00).
"""

import os
import subprocess
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent
ARCH_FILE = ROOT / "ARCHITECTURE.md"

claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))


def read_file(path: Path) -> str:
    return path.read_text() if path.exists() else "(file not found)"


def get_git_log() -> str:
    result = subprocess.run(
        ["git", "log", "--pretty=format:--- %n%h  %ad  %an%n%s%n%n%b", "--date=short", "--stat"],
        capture_output=True, text=True, cwd=ROOT,
    )
    return result.stdout


def get_file_tree() -> str:
    result = subprocess.run(
        ["find", ".", "-maxdepth", "1", "-name", "*.py", "-o", "-name", "*.md"],
        capture_output=True, text=True, cwd=ROOT,
    )
    return result.stdout.strip()


def run_update():
    arch = read_file(ARCH_FILE)
    git_log = get_git_log()
    file_tree = get_file_tree()

    print("Reading codebase and git history...")

    source_files = {}
    for fname in ["models.py", "main.py", "seed_db.py", "tag_bills.py", "ai_tools.py", "services.py", "seed_candidates.py"]:
        p = ROOT / fname
        if p.exists():
            source_files[fname] = p.read_text()

    source_dump = "\n\n".join(
        f"=== {name} ===\n{content}" for name, content in source_files.items()
    )

    import datetime
    today = datetime.date.today()

    prompt = f"""You are maintaining the ARCHITECTURE.md for a project called StrawPoll Voting App.
This is a US congressional vote tracking app with an AI chat interface.

Your two jobs:
1. Update ARCHITECTURE.md to reflect everything in the git log that isn't already documented
2. Flag any discrepancies where ARCHITECTURE.md says something that contradicts the actual code

Here is the current ARCHITECTURE.md:
<architecture>
{arch}
</architecture>

Here is the full git log with file stats (every commit):
<git_log>
{git_log}
</git_log>

Here are the current source files:
<source>
{source_dump}
</source>

File tree:
<file_tree>
{file_tree}
</file_tree>

Instructions:
- Rewrite ARCHITECTURE.md completely with all updates applied. Keep the same structure and tone.
  Add a "Last synced" date at the top: {today}.
- After the full ARCHITECTURE.md, add a section called ## Discrepancies Found listing anything
  that doesn't match between the docs and the code. If nothing is wrong, write "None found."
- Keep the narrative style — this document is meant to explain the project to non-technical people
  as well as developers. Explain the WHY not just the WHAT.
- Update the "Current Data Stats" section if the git log shows the DB was re-seeded.
- Update the Roadmap checkboxes based on what the git log shows was completed.

Return ONLY the full updated ARCHITECTURE.md content followed by the discrepancy section.
Do not add any preamble or explanation outside the document itself.
"""

    print("Sending to Claude (this may take 30-60 seconds)...")

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    result = response.content[0].text

    if "## Discrepancies Found" in result:
        arch_part, discrepancy_part = result.split("## Discrepancies Found", 1)
        arch_part = arch_part.strip()
        discrepancy_part = "## Discrepancies Found" + discrepancy_part
    else:
        arch_part = result.strip()
        discrepancy_part = None

    ARCH_FILE.write_text(arch_part)
    print(f"✓ ARCHITECTURE.md updated ({len(arch_part)} chars)")

    if discrepancy_part:
        print("\n" + "="*60)
        print(discrepancy_part)
        print("="*60)
    else:
        print("✓ No discrepancies section found in response")

    tokens_used = response.usage.input_tokens + response.usage.output_tokens
    print(f"\nTokens used: {tokens_used:,}")


if __name__ == "__main__":
    run_update()
