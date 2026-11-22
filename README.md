# linux-assist (`la`)

A lookup-only Linux command reference for Ubuntu 22.04 and 24.04 servers in an
air-gapped environment. It runs in a Docker container. It never runs a
command and never reads the server. Design: [DESIGN.md](DESIGN.md).

## Use

```
la show ss                       # KB card, or the man page summary
la show ss --man                 # the whole man page
la show kubectl rollout status   # a docker / kubectl subcommand
la find "free up disk space"     # which command does this?
la explain "sudo ss -tulpn"      # every part, checked in the man pages
la why "No space left on device" # known causes, and how to check each
journalctl -u nginx | la why     # the same, over a log
la guide list                    # step-by-step guides
la guide lvm-extend VG=ubuntu-vg # one guide, your values filled in
df -h | la interpret             # fixed rules over piped output
kubectl get pods -A | la interpret   # pods / docker ps -a too
la ask "which process listens on port 443"   # AI, every command checked
la version
```

- Answers match this server's release; `--release 22.04` shows the other.
- Labels: ✅ KB, checked · `[man]` from the man page · `[help]` from
  `--help` text · 🟡 AI-written, checked · 🔴 not in the man page.
- Docker and kubectl: checked against the versions in the VERSIONS
  block at the top of the `Dockerfile` (docker-ce-cli 28.5.1,
  docker-compose-plugin 2.40.2, kubectl 1.34.13). Change them to match
  the servers, then rebuild.

## Dev

```
scripts/build.sh          # build (KB check inside), tests, Trivy scan
./la show ss              # run from the checkout
scripts/make-bundle.sh    # the prod bundle: dist/la-<version>.tar
```

- Settings: the block at the top of `compose.yaml`. `.env` holds only
  `LLM_API_KEY` and is never in git.
- KB sources: `kb/cmd/*.json`, `kb/guides/*.json`, `kb/errors.json`. A
  wrong flag, a missing package or a wrong danger label fails the build.
- Tests only: `docker run --rm --network none --read-only --tmpfs /tmp -v "$PWD/tests:/tests:ro" --entrypoint python la:1.0 -m unittest discover -s /tests`

## Prod

Nothing is built on a server. On dev, `scripts/make-bundle.sh` makes
`dist/la-<version>.tar`, which holds the pre-built image. Carry it and
its `.sha256` to the server, then follow `INSTALL.txt` inside the tar.
In short: `docker load`, put the vLLM host (or the GPT-5.4 block) in
`compose.yaml`, then `sudo ln -sf /opt/la/la /usr/local/bin/la`.
How to use it: [USAGE.txt](USAGE.txt) (in the bundle too).

## Continuing this work in Claude Code

From this repository's checkout: `claude --teleport` lists the earlier
sessions to pick from. Or start fresh with `claude` and say "Read DESIGN.md".
