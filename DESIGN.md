# linux-assist (`la`) — Design

Status: design agreed, implementation not started.

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
3. **Lookup only.** The tool never runs commands and never reads the host.
   The user runs the commands themselves.

## Decisions

| Topic | Decision |
|---|---|
| Target releases | Ubuntu 22.04 (jammy) and 24.04 (noble) |
| Interface | Plain command line (no TUI in v1) |
| Language | Python 3, standard library only |
| Deployment | **Option C:** Docker container, lookup only |
| Command execution | None (scope reduced from earlier "can run commands") |
| AI | Not in v1 (see Open questions) |

### Rejected deployment options (for the record)

- **A — native `.deb`:** full host access. Dropped in favour of a container.
- **B — privileged container** (`--privileged --pid=host` + `nsenter`):
  effectively root on the host, so it defeats the isolation a container exists to give.

## Image layout

One image covers both releases.

```
la:1.0
├── /opt/la/app/        Python code (stdlib only)
├── /opt/la/kb/         curated command cards, guides, error entries
├── /opt/la/man/jammy/  man pages from stock Ubuntu 22.04 packages
├── /opt/la/man/noble/  man pages from stock Ubuntu 24.04 packages
└── /opt/la/index.db    search index built at image build time
```

- **Multi-stage build.** A 22.04 stage and a 24.04 stage each install the admin
  packages and copy out `/usr/share/man`.
  - Ubuntu's Docker images strip man pages, so the build must restore them.
  - Admin packages include lvm2, mdadm, nftables, ufw, netplan, openssh,
    iproute2 and the systemd tools.
- **Limit of flag checks:** they are only as good as **stock Ubuntu packages
  as of the build date**.
  - Every answer header shows the release and the build date.
  - Third-party software is not covered unless it is added to the build.

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

1. Every example parses with `shlex`.
2. Every flag in every example exists in the man page of each release the
   example is tagged for.
3. Every example has a danger level.

## Flows

### Flow 0: build, deliver, set up

On a machine with internet access:

```
docker build -t la:1.0 .          # runs the KB checks
docker save la:1.0 | gzip > la_1.0.tar.gz
sha256sum la_1.0.tar.gz > la_1.0.tar.gz.sha256
```

Move both files across the air gap. Then on each server:

```
sha256sum -c la_1.0.tar.gz.sha256
docker load < la_1.0.tar.gz
```

Shell alias (`~/.bashrc`):

```
alias la='docker run --rm -i --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user 65534 \
  -e LA_RELEASE=$(. /etc/os-release; echo $VERSION_ID) la:1.0'
```

- `LA_RELEASE` is the only host information passed in. It is read by the alias
  on the host.
- `--release 22.04|24.04` overrides it.

### Flow 1: `la show <command>`

1. KB card.
2. Otherwise the man page for the selected release.
3. Otherwise "not found in <release> stock packages".

### Flow 2: `la find "<task>"`

Search KB tasks first, then man page descriptions. Results are ranked, and
each shows its source.

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

### Flow 6 (optional, undecided): `<cmd> | la interpret <cmd>`

- The user pipes command output in. The container only receives it on stdin
  and applies fixed rules (e.g. `df` usage over 85%).
- Nothing is stored and nothing leaves the machine.

## Answer labels

- ✅ From the KB, with checks passed.
- `[man]` Taken directly from a man page.
- 🔴 Flag not found in the man page for the release.

## Removed from scope (were in earlier drafts)

- Health checks (`la check`)
- Running commands, execution safety levels, and the audit log
- `la last` (needed a hook on the host)
- AI troubleshooting (`la diagnose`)

## AI: deferred design (not in v1)

If added later, as a separate image tag `la:1.0-ai`:

1. **Retrieve first.** The tool pulls the relevant KB entries and man pages.
2. **Model call.** The self-hosted model answers at temperature 0, as
   structured JSON that must cite those sources.
3. **Mechanical check.** Every command the model suggests is checked: binary
   exists for the release, and every flag exists in the man page.
   - On failure, one retry with the error.
   - If it fails again, the answer is shown as 🔴 rejected.
4. **Label.** Answers are labelled 🟡 (AI-written, flags checked). The tool
   still works fully when the model is unreachable.

**Network trade-off:** AI removes the `--network none` guarantee. The
container would need network access to the model host only.

## Open questions

1. **Where does it run:** on each server, or on one jump host (bastion)?
2. **Flow 6** (interpreting piped output): include it in v1?
3. **AI:**
   - Exact models. "Nemotron" and "gpt-5 4" were mentioned, but the exact
     model names are not confirmed.
   - Endpoint URL and API type (OpenAI-compatible `/v1/chat/completions`?).
   - Login method (API key or none).
   - Network path to the model host.
   - Data rules: may command text be sent, or must it be masked first?

## Phase 1 plan

1. Dockerfile with multi-stage man page collection for 22.04 and 24.04.
2. KB format, plus about 100 command cards.
3. KB checks run during the build.
4. Flows 0–5.
