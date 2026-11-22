# linux-assist (`la`) — Design

Status: Phase 1 done (steps 1-7), 2026-11-21. How to build, run and ship:
see README.md.

## Goal

A plain command-line reference tool for Linux admin work on Ubuntu servers,
covering commands from basic to advanced, usable in an air-gapped environment.

## Hard rules

1. **No guessing.** Every answer comes from a cited source:
   - the curated knowledge base (KB), or
   - man pages for the selected Ubuntu release.
   If there is no source, the tool says "not found". It never invents an answer.
2. **Air-gapped.** No network access at runtime, no telemetry, no packages
   fetched at runtime.
   - One exception: `la ask` talks to the configured model URL, and only
     that URL. Every other command runs with no network at all.
3. **Lookup only.** The tool never runs commands and never reads the host.
   The user runs the commands themselves.
   - The wrapper passes in three values only: the release (from
     `/etc/os-release`), the host name (so masking can hide it) and the
     terminal width.

## Decisions

- **Releases:** Ubuntu 22.04 (jammy) and 24.04 (noble).
- **Interface:** plain command line (no TUI in v1).
- **Language:** Python 3, standard library only.
- **Deployment:** Option C, a lookup-only Docker container.
- **Where:** on each server.
- **Command execution:** none.
- **Flow 6** (interpret piped output): in v1.
- **AI:** in v1, as `la ask` (see "AI" below).
  - Dev: Nemotron Ultra on NVIDIA's API.
  - Prod: Nemotron Super on vLLM, or GPT-5.4. Both are inside the air gap.

### Rejected deployment options (for the record)

- **A — native `.deb`:** full host access. Dropped in favour of a container.
- **B — privileged container** (`--privileged --pid=host` + `nsenter`):
  effectively root on the host, so it defeats the isolation a container exists to give.

## Image layout

One image covers both releases.

```
la:1.0  (python:3.13-alpine, pip removed, one layer)
├── /opt/la/app/la/        Python code (stdlib only)
├── /opt/la/kb.json        the checked KB, written by the build
├── /opt/la/data/22.04/    man pages (plain text), whatis.tsv,
└── /opt/la/data/24.04/    commands.tsv, meta.json
```

- **Base image:** `python:3.13-alpine`, not Debian `python:3.13-slim`.
  - Trivy on 2026-11-20: Debian slim had 53 HIGH findings, almost all with
    no fix available. Alpine had 5, all inside pip, which the build removes.
  - `scripts/build.sh` builds and then scans. It fails on any HIGH or
    CRITICAL finding.
- **KB source files:** one JSON file per category (`kb/cmd/network.json`,
  ...), not one file per command.

- **Multi-stage build.** A 22.04 stage and a 24.04 stage each install the admin
  packages and copy out `/usr/share/man`.
  - Ubuntu's Docker images strip man pages, so the build must restore them.
  - Admin packages include lvm2, mdadm, nftables, ufw, netplan, openssh,
    iproute2 and the systemd tools.
- **Limit of flag checks:** they are only as good as **stock Ubuntu packages
  as of the build date**.
  - Every answer header shows the release and the build date.
  - Third-party software is not covered unless it is added to the build.
- **Docker and Kubernetes** are the two third-party tools added. Their
  versions are pinned in the VERSIONS block at the top of the `Dockerfile`:
  - `docker-ce-cli` 28.5.1 and `docker-compose-plugin` 2.40.2 from
    download.docker.com (signing key checked against its fingerprint).
    `docker-ce-cli` ships 190 man pages, one per subcommand
    (`docker-container-ls(1)`).
  - kubectl 1.34.13 from dl.k8s.io (sha256 checked), the same version as
    the troubleshooter app uses for VKS.
  - kubectl and docker compose ship no man pages. `build/helpman.py` runs
    `--help` for every subcommand (107 kubectl, 37 compose) and writes
    man-style pages, labelled `[help]` and cited as `kubectl get --help`.
    It also records aliases (`kubectl top pods` → `kubectl top pod`).
  - The binaries run only inside the Ubuntu build stages; only text
    reaches the final image, so Trivy never sees them.
  - Not covered: kubectl plugins (`kubectl vsphere login`), docker
    plugins other than compose (buildx), and crictl.

## KB entry contents

- **Command card:**
  - name, summary, package
  - examples tagged BASIC / INTERMED / ADVANCED
  - pitfalls, see-also
  - danger level per example: READ-ONLY / MODIFY / DESTRUCTIVE
  - releases verified on
- **Guide** (read-only runbook):
  - steps: check → prepare → change → verify, with rollback notes
  - placeholders such as `<VG>` stay as placeholders, with the command
    that reveals each value
- **Error entry:** error text pattern, known causes, a command to confirm each cause.

### Build-time check (the build fails if any check fails)

Every runnable line (card examples, guide steps and rollbacks, error checks
and fixes, placeholder "find it" commands) is checked on each release:

1. It parses (pipes, lists, redirects, `sudo`/`xargs`/`watch` wrappers,
   `find -exec`, `$( )`).
2. Each command is in that release's stock packages (or is docker or
   kubectl, see above).
3. Each flag and subcommand is in its man page. For docker and kubectl
   the subcommand words pick the page (`kubectl rollout status` →
   `kubectl rollout status --help`), a flag is checked against the pages
   for where it stands (`docker compose -f FILE logs -f`: compose's
   `--file`, then logs' `--follow`), a flag's value is skipped when its
   option line says it takes one (`--namespace=''` yes, `--detach[=false]`
   no), and after `docker run IMAGE` / `exec CONTAINER` / `kubectl exec
   POD --` the rest is the command run inside, which is not checked.
4. Its danger label matches the destructive patterns (`rm -r`, `mkfs`,
   `dd of=`, `find -delete`, ...); modify and destructive guide steps
   have a rollback.
5. `see_also` names only commands the build knows.

Examples that pass only on one release are tagged with it (e.g. `apt policy`
is 24.04 only) and shown as "not on 22.04" there.

## Flows

### Flow 0: build, deliver, set up

On the dev machine (internet access):

```
scripts/make-bundle.sh
  │
  ▼
scripts/build.sh: docker build (KB check inside)
  → tests in the image (no network) → Trivy
  │   any failure → stop, no bundle
  ▼
dist/la-<version>.tar + .sha256
  (image.tar.gz pre-built, compose.yaml without
   the dev model, la wrapper, USAGE.txt,
   env.example, INSTALL.txt)
```

Move both files across the air gap. Nothing is built on a server.
On each server, INSTALL.txt: check the checksums, `docker load`,
fill in the model host in `compose.yaml`, link `/opt/la/la` to
`/usr/local/bin/la`. USAGE.txt is the user guide: every command
with examples, the labels, exit codes and what to do on errors.

The bundle provides:

- `compose.yaml` with a settings block at the top (model URL, model name,
  thinking, reply size, timeout, TLS check, CA file, masking, mask words).
  `.env` holds only `LLM_API_KEY` (dev, and GPT-5.4).
- Two services in that one file:
  - `la`: `network_mode: none` (loopback only). Used by every command
    except `ask`.
  - `la-ask`: Docker's default bridge, so no network is created on the
    host. Used only by `la ask`.
  - Both: read-only, all capabilities dropped, no-new-privileges,
    user 65534, no host folders, `pull_policy: never` (never
    reaches a registry).
- A small wrapper, `/usr/local/bin/la`:

```
la show ss
  │
  ▼
wrapper: image loaded? no → "docker load ..." (exit 4)
  │
  ▼
wrapper reads /etc/os-release → LA_RELEASE=24.04
  │
  ├─ "ask"?  → docker compose run --rm -T la-ask ...
  └─ else    → docker compose run --rm -T la ...
  │
  ▼
container answers, is removed (about 0.4 s)
```

- Never a TTY in the container (`-T`), so output pipes cleanly
  (`la show ss | grep -i port`).
- Typed at a terminal: stdin is `/dev/null`. Piped: the container
  reads it. `la ask` waits at most 3 s for piped text, so an open but
  silent stdin (cron, ssh without -t) can't hang it.
- `--release` overrides `LA_RELEASE`.
- No host firewall or network changes. `la-ask` is limited to the model URL
  by the app itself, not by the network.

### Flow 1: `la show <command>`

1. KB card.
2. Otherwise the man page for the selected release.
3. Otherwise "not found in <release> stock packages".

### Flow 2: `la find "<task>"`

Word search, no model: card names and tasks score most, then summaries
and example descriptions, then man page descriptions (whatis). Rare words
count more than common ones, and a hit that covers more of the words
ranks higher. Each result shows its source and one example.

### Flow 3: `la explain "<command line>"`

1. Split the line with `shlex`.
2. Match each flag to the man page text for the release.
3. Mark flags that are not in the man page with 🔴.
4. Show the danger level and quoting pitfalls.

### Flow 4: `la why "<error text>"`

Match the pasted text against error entries. Show known causes and a command
to confirm each one.

### Flow 5: `la guide list` / `la guide <name>`

Print a read-only runbook with danger labels, verify steps and rollback notes.

### Flow 6: `<cmd> | la interpret <cmd>`

- The user pipes command output in. The container only receives it on stdin
  and applies fixed rules. The kind is guessed from the headers, or named.
  - `df`: 85% used warns, 95% is critical (loop, tmpfs, snap left out).
  - `free`: available memory 10% or less warns, 5% is critical.
  - `uptime`: load above the CPU count (`--cpus N`).
  - `vmstat`: swapping (si/so above 0), disk wait 20% or more.
  - `iostat -x`: %util 80% warns, 95% is critical.
  - `ss`: what listens on all interfaces, one address, loopback.
  - `systemctl`: failed and not-found units.
  - `lsblk`: disks with no partitions and no mount point.
  - `apt list --upgradable`: counts, and which come from -security.
  - `pods` (`kubectl get pods`): not Running or Completed, running but
    not ready, 5 or more restarts.
  - `docker` (`docker ps -a`): restarting, unhealthy, dead, exited with a
    code other than 0 or 143 (137 = killed, often memory).
  - `log` (journalctl, dmesg): counts the known errors from errors.json.
- Every NEXT line is a KB guide or a KB example, never a new command.
- Exit code 1 when a rule warned, so it can be used in scripts.
- Nothing is stored and nothing leaves the machine.

### Flow 7: `la ask "<question>"`

```
question
  │
  ▼
retrieve KB cards + man pages (release)
  │
  ▼
mask host names / IPs / users
  │
  ▼
model (temperature 0, JSON + sources)
  │
  ▼
check each command: binary exists?
flags in man page? danger level?
  │
  ├─ pass → show 🟡 with sources
  └─ fail → retry once → still fail → 🔴
```

- If the model is unreachable, `la ask` says so and falls back to `la find`.
- Each step shows its own label: ✅ when it is exactly a KB example,
  🟡 when the AI wrote it and the check passed (with the man pages used),
  🔴 "do not run" with the reason when it failed twice.
- `la ask -v` prints each step in plain English on stderr ("Masked
  before sending: 1 host name, 1 IP address.").

## Answer labels

- ✅ From the KB, with checks passed.
- `[man]` Taken directly from a man page.
- `[help]` Taken from `--help` text (kubectl, docker compose).
- 🟡 Written by the AI; flags checked against the man pages.
- 🔴 Flag not found in the man page for the release.

## Removed from scope (were in earlier drafts)

- Health checks (`la check`)
- Running commands, execution safety levels, and the audit log
- `la last` (needed a hook on the host)
- AI troubleshooting (`la diagnose`)

## AI

### Models

Settings follow `/apps2/troubleshooter/compose.example.yaml`.

- **Dev:** `nvidia/nemotron-3-ultra-550b-a55b`
  - URL `https://integrate.api.nvidia.com/v1`, key `LLM_API_KEY` in `.env`.
  - Tested 2026-11-20: listed by `/v1/models`, answered in 5.3 s.
  - It is outside our network, and free tiers may train on prompts,
    so masking is always on in dev.
- **Prod:** `nvidia/nemotron-3-super-120b-a12b` on vLLM, no key.
  - URL `http://<vllm-host>:8000/v1`. The host isn't known yet.
  - The model ID comes from the troubleshooter config. It is NOT tested
    from here, because prod is air-gapped.
- **Prod, other choice:** `gpt-5.4`, URL `https://<gpt-host>/v1`, key
  `LLM_API_KEY` in `.env`.
  - GPT takes `max_completion_tokens` and `reasoning_effort`; Nemotron takes
    `max_tokens` and `chat_template_kwargs`. The client sends the right ones
    (as troubleshooter's `llm.py` does).
- **Settings file:** both prod blocks ship commented out, with placeholder
  URLs. The admin fills in the host and keeps ONE block.
- **Masking:** on by default for every model. It can be turned off in the
  settings block.
- **Prod bundle:** the dev block is removed when the bundle is made, so the
  online model can't stay switched on in prod (same as troubleshooter's
  `make-bundle.sh`).

### Known issues

- **Thinking can use up the whole reply.** Nemotron 3.5 Super (dev) spent all
  200 reply tokens thinking and returned no answer. Handled:
  - `llm-reasoning` is `off` by default and sent as
    `chat_template_kwargs.enable_thinking` (Nemotron) or
    `reasoning_effort` (GPT); replies get 2000 tokens.
  - Tested 2026-11-21 on dev: Ultra answers in 1.5 s (thinking off) and
    4 s (on); 3.5 Super in 1.8 s and 3.5 s. Both give clean JSON.
  - If a reply is all thinking, `la ask` says so and names the setting.
- **Dev and prod models differ** (550B Ultra and 120B Super). Prod answers
  may be weaker than dev. The command check catches bad flags, not weaker
  wording.
- **The exact prod model can't be tested on the dev API.**
  `nemotron-3-super` isn't listed there; only `3.5-super-vl` is.

### How an answer is made

1. **Retrieve first.** The tool pulls the relevant KB entries and man pages.
2. **Model call.** The self-hosted model answers at temperature 0, as
   structured JSON that must cite those sources.
3. **Mechanical check.** Every command the model suggests is checked: binary
   exists for the release, and every flag exists in the man page.
   - On failure, one retry with the error.
   - If it fails again, the answer is shown as 🔴 rejected.
4. **Label.** Answers are labelled 🟡 (AI-written, flags checked). The tool
   still works fully when the model is unreachable.

### Masking

- Found in one pass over the original text, then replaced:
  - IPs become `198.18.0.N`, IPv6 `2001:db8::N`, MACs `02:00:00:00:..`;
  - host names become `hostA.example.com`; users `usera`
    (`user@host`, `/home/<user>`);
  - this server's host name and the `mask-words` become `nameA`.
- Stand-ins are valid in commands, and the answer gets the real values
  back. Loopback, netmasks, Ubuntu's own hosts, file names
  (`app.conf`) and versions (`1.2.3`) are left alone.

## Code map

- `cli.py`: arguments, `--release`, stdin.
- `show.py`, `find.py` + `search.py`, `explain.py`, `why.py`, `guide.py`,
  `interpret.py`, `ask.py` + `llm.py` + `mask.py`: one file per flow.
- `verify.py`, `cmdline.py`, `manpage.py`, `danger.py`: the checks, shared
  by the build-time KB check (`check.py`) and the lookups.
- `kb.py` loads the KB sources at build time; `kbdata.py` reads the
  checked `kb.json` at run time; `out.py` wraps text at 80 columns or less.
- `build/collect.sh` + `collect.py`: stock packages and their man pages.
  `build/collect-extra.sh`: Docker's CLI and kubectl at the pinned
  versions. `build/helpman.py`: pages from `--help`.

### Danger rules for docker and kubectl

- Destructive: `docker rmi`, `docker image rm`, any `prune` (images can't
  be pulled again on an air-gapped server), `docker volume rm`, `down -v`
  / `rm -v`, `kubectl delete`, `kubectl drain --delete-emptydir-data`,
  `kubectl replace --force`.
- Modify: removing an ordinary container (`docker rm`, `compose down`),
  `exec`, `rollout restart`, `scale`, `cordon`, `config use-context`.
- Read-only: `ps`, `logs`, `inspect`, `get`, `describe`, `top`,
  `rollout status`, `config view`, `auth can-i`, ...
- `la explain` also warns before a kubectl change: check the context.

## Phase 1 plan (done)

1. Dockerfile with multi-stage man page collection for 22.04 and 24.04.
2. KB format, plus 133 command cards, 11 guides, 29 error entries
   (now 135 cards, 14 guides, 39 error entries with Docker and kubectl).
3. KB checks run during the build.
4. `compose.yaml` (settings block, two services) and the `la` wrapper.
5. Flows 0–6.
6. Flow 7 (`la ask`) against dev Ultra, with the prod Super and GPT-5.4
   blocks ready (commented out).
7. Bundle script for prod (removes the dev model block).

Not tested from here: the prod models themselves (air-gapped). The GPT
request shape is covered by a unit test only.
