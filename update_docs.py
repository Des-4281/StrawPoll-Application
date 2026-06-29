"""
On-demand ARCHITECTURE.md updater and discrepancy checker.

Reads the codebase and git history, asks Claude what needs to change in ARCHITECTURE.md,
then walks you through each proposed edit one at a time — approve, skip, or rewrite it.

  python update_docs.py

You control when tokens are spent. One run per session is typical (~$0.50-1.00).
"""

import difflib
import json
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


def color_diff(old: str, new: str) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, fromfile="current", tofile="proposed", lineterm=""))
    out = []
    for line in diff:
        if line.startswith('+') and not line.startswith('+++'):
            out.append(f"\033[32m{line}\033[0m")
        elif line.startswith('-') and not line.startswith('---'):
            out.append(f"\033[31m{line}\033[0m")
        elif line.startswith('@@'):
            out.append(f"\033[36m{line}\033[0m")
        else:
            out.append(line)
    return "\n".join(out) if out else f"\033[32m{new}\033[0m"


def collect_edit(section: str) -> str:
    print(f"\n  Rewrite the proposed text for '{section}'.")
    print("  (Enter your text. Type a single '.' on its own line when done.)\n")
    lines = []
    while True:
        line = input()
        if line == ".":
            break
        lines.append(line)
    return "\n".join(lines)


def prompt_change(change: dict, index: int, total: int) -> str | None:
    """
    Show one proposed change and loop until the user approves, skips, or edits.
    Returns the final new text to apply, or None if skipped.
    """
    section = change.get("section", "Unknown section")
    description = change.get("description", "")
    old_text = change.get("old", "")
    new_text = change.get("new", "")

    while True:
        print(f"\n{'─' * 60}")
        print(f"  [{index}/{total}] {section}")
        print(f"  {description}")
        print(f"{'─' * 60}")
        print(color_diff(old_text, new_text))
        print()

        choice = input("  Apply? [y]es  [n]o  [e]dit: ").strip().lower()

        if choice == "y":
            return new_text
        elif choice == "n":
            return None
        elif choice == "e":
            new_text = collect_edit(section)
        else:
            print("  Enter y, n, or e.")


def run_update():
    import datetime
    today = datetime.date.today()

    arch = read_file(ARCH_FILE)
    git_log = get_git_log()
    file_tree = get_file_tree()

    print("Reading codebase and git history...")

    source_files = {}
    for fname in [
        "models.py", "main.py", "seed_db.py", "tag_bills.py", "ai_tools.py",
        "services.py", "seed_candidates.py", "summarize_bills.py", "describe_bills.py",
    ]:
        p = ROOT / fname
        if p.exists():
            source_files[fname] = p.read_text()

    source_dump = "\n\n".join(
        f"=== {name} ===\n{content}" for name, content in source_files.items()
    )

    prompt = f"""You are reviewing ARCHITECTURE.md for the StrawPoll Voting App and identifying what needs to be updated.

Here is the current ARCHITECTURE.md:
<architecture>
{arch}
</architecture>

Here is the full git log:
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

Today's date: {today}

Your job: identify every place in ARCHITECTURE.md that needs to be added or changed to reflect the current state of the codebase and git history.

Return a JSON array of proposed edits. Each edit has:
  - "section": the section name (e.g. "Roadmap", "Database Schema", "Repository Structure")
  - "description": one sentence describing what is changing and why
  - "old": the EXACT text from ARCHITECTURE.md to replace — copy it verbatim, character for character
  - "new": the replacement text

Rules:
- "old" MUST be an exact substring of ARCHITECTURE.md as provided above. Do not paraphrase or reformat it.
- For pure additions with no existing text to replace, set "old" to "" and put the full new block in "new".
- Only propose changes where something is actually wrong or missing. Do not propose cosmetic rewrites of things that are already correct.
- Update the "Last updated" and "Last synced" dates at the top.
- Update roadmap checkboxes if the git log shows a feature was completed.
- If a column, endpoint, script, or flag exists in the code but is absent from the docs, add it.

Return ONLY the JSON array. No explanation, no markdown fences — raw JSON only.
"""

    print("Sending to Claude (this may take 30-60 seconds)...")

    response = claude.messages.create(
        model="claude-opus-4-8",
        max_tokens=32000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if Claude included them anyway
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    raw = raw.strip()

    try:
        changes = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"\n✗ Claude returned invalid JSON: {e}")
        print("Raw response preview:")
        print(raw[:1000])
        return

    tokens_used = response.usage.input_tokens + response.usage.output_tokens
    print(f"Tokens used: {tokens_used:,}")
    print(f"Proposed changes: {len(changes)}")

    if not changes:
        print("\nNo changes needed — ARCHITECTURE.md is already up to date.")
        return

    doc = arch
    applied = 0
    skipped = 0

    for i, change in enumerate(changes, start=1):
        approved_text = prompt_change(change, i, len(changes))

        if approved_text is None:
            skipped += 1
            continue

        old_text = change.get("old", "")
        if old_text:
            if old_text in doc:
                doc = doc.replace(old_text, approved_text, 1)
                applied += 1
            else:
                print(f"\n  ⚠ Could not find exact text in document — skipping this change.")
                print(f"    Searched for: {old_text[:100]}...")
                skipped += 1
        else:
            doc = doc.rstrip() + "\n\n" + approved_text + "\n"
            applied += 1

    print(f"\n{'=' * 60}")
    print(f"  Applied: {applied}   Skipped: {skipped}")

    if applied > 0:
        ARCH_FILE.write_text(doc)
        print(f"  ✓ ARCHITECTURE.md saved ({len(doc):,} chars)")
    else:
        print("  No changes written.")


if __name__ == "__main__":
    run_update()
