#!/usr/bin/env python3
"""
codex_relay.py -- thin wrapper around `codex exec` for AgentOS.

Routes a prompt to Codex CLI (GPT-5.4, ChatGPT Plus OAuth, no API billing)
and returns only the clean response text, stripping the codex header/metadata.

Usage:
    # Prompt from stdin
    echo "Write a caption for Vayu" | python3 scripts/codex_relay.py

    # Prompt from args
    python3 scripts/codex_relay.py "Write a caption for Vayu"

    # Agent Bash call pattern
    response=$(echo "$PROMPT" | python3 /Users/celainc/Developers/ClaudeAgentSDK/scripts/codex_relay.py)
"""

import sys
import subprocess
import re


def extract_response(output: str) -> str:
    """Extract the clean model response from codex exec output.

    codex exec stdout format:
        Reading prompt from stdin...
        OpenAI Codex vX.Y.Z
        --------
        workdir: ...
        model: gpt-5.4
        ...
        --------
        user
        <prompt>

        codex
        <response>
        tokens used
        <N>
        <response again>

    We extract the block between 'codex\\n' and '\\ntokens used'.
    """
    parts = output.split("\ncodex\n", 1)
    if len(parts) < 2:
        # Fallback: return raw output minus the header (up to first blank line after second ---)
        return output.strip()

    response_block = parts[1]
    # Stop before "tokens used"
    response = response_block.split("\ntokens used")[0].strip()
    return response


def main() -> None:
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    else:
        prompt = sys.stdin.read().strip()

    if not prompt:
        print("Error: no prompt provided", file=sys.stderr)
        sys.exit(1)

    try:
        result = subprocess.run(
            ["codex", "exec"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except FileNotFoundError:
        print(
            "Error: codex CLI not found. Install with: npm install -g @openai/codex",
            file=sys.stderr,
        )
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: codex exec timed out after 90s", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0 and not result.stdout:
        print(f"Error from codex: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(result.returncode)

    clean = extract_response(result.stdout)
    print(clean)


if __name__ == "__main__":
    main()
