# linux-assist (`la`)

A lookup-only Linux command reference for Ubuntu 22.04 and 24.04 servers in an
air-gapped environment. It runs in a Docker container.

Status: design agreed, implementation not started. See [DESIGN.md](DESIGN.md).

## Continuing this work in Claude Code on a Linux terminal

The machine needs internet access to reach Anthropic. Don't use one of the
air-gapped servers.

1. Install Claude Code:
   ```
   curl -fsSL https://claude.ai/install.sh | bash
   # or: npm install -g @anthropic-ai/claude-code
   ```
2. Log in with the same claude.ai account used for the original session.
   Run `claude` and follow the login prompt, or use `/login`.
3. Clone the repository:
   ```
   git clone https://github.com/j6dtt/linux-assist.git
   cd linux-assist
   ```
4. Pull the original cloud session, including its conversation, into the
   terminal (teleport):
   ```
   claude --teleport session_01UWRC9sz2Ro5bC3mp65Y1DQ
   ```
   - `claude --teleport` with no ID shows a list of sessions to pick from.
   - Inside a running Claude Code session, type `/teleport`.
   - The working tree must be clean. Run it from this repository's checkout.

### If teleport doesn't work

All decisions are in `DESIGN.md`, so start a fresh session instead:

```
git checkout claude/linux-command-reference-8r8tlz
claude
```

Then tell Claude: **"Read DESIGN.md, then I'll answer the open questions
before you start Phase 1."**

Docs: https://code.claude.com/docs/en/claude-code-on-the-web
