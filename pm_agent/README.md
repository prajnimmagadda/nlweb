# PM Copilot

A personal AI project manager for agile software projects, grounded in the PMBOK® Guide
(8th edition). It keeps a versioned record of your project, computes metrics and forecasts
in code, and exposes PMBOK-based playbooks to Claude over MCP. The design is in
[`docs/agentic-pm/PLAN.md`](../docs/agentic-pm/PLAN.md).

**Status:** Phase 0 (foundations) is done, plus the first six Project-agent playbooks.
GitHub access is read-only. Nothing is sent or written outside your machine except by your
MCP host.

## How it fits together

- **Claude is the agent.** You connect this MCP server to Claude Code or Claude Desktop.
  Claude runs a playbook (an MCP prompt), calls the tools here, and can combine them with
  its own Google Drive, Calendar and Gmail connectors.
- **Code does the numbers.** Flow metrics, Monte Carlo forecasts, agile earned value and
  RAG status are computed by the engines. The model never picks a colour or a date.
- **You stay accountable.** The agent can propose risks, decisions and change requests,
  but only a human can approve them, approve a charter, or change RAG thresholds. The
  store enforces this on every write, and those commands exist only in the CLI.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r pm_agent/requirements.txt
python -m pytest pm_agent/tests          # optional
```

Run commands from the repo root. Data lives in `~/.pm-copilot/pm.db`; override it with
`PM_COPILOT_DB` or `--db`. The CLI records you as `human:<name>`, using `PM_COPILOT_USER`,
then your git `user.name`, then your login name.

### Try it without real data

```bash
python -m pm_agent seed-demo      # synthetic project "demo": 12 weeks of history, 2 risks
python -m pm_agent health demo    # computed RAG per dimension
python -m pm_agent forecast demo  # P50-P95 completion dates for the v1 milestone
```

### Use it on a real project

```bash
export GITHUB_TOKEN=...   # fine-grained token, read-only "Issues" (and "Metadata") on your repos
python -m pm_agent init myproj --name "Checkout v2" --repo you/app \
    --start 2026-09-01 --target 2026-12-15 --milestone v1
python -m pm_agent sync myproj
python -m pm_agent health myproj
```

GitHub conventions it understands:

| What | How |
|---|---|
| Release scope | Issues in the milestone given by `--milestone` |
| In progress / blocked | Labels `in progress` / `blocked` (configurable in the profile) |
| Epic / bug / task | Labels `epic` / `bug`, or GitHub's native issue types |
| Story points | Labels such as `points:3`, `sp:3` or `estimate:3` |
| Done vs dropped | Closed as completed vs closed as not planned |

## Connect it to Claude

**Claude Code** (run from the repo root, using your venv's Python):

```bash
claude mcp add pm-copilot --scope user \
  --env PYTHONPATH=$PWD --env GITHUB_TOKEN=$GITHUB_TOKEN \
  -- $PWD/.venv/bin/python -m pm_agent serve
```

The playbooks then appear as slash commands such as
`/mcp__pm-copilot__monitor_and_control_performance demo`.

**Claude Desktop:** add this to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pm-copilot": {
      "command": "/path/to/nlweb/.venv/bin/python",
      "args": ["-m", "pm_agent", "serve"],
      "env": { "PYTHONPATH": "/path/to/nlweb", "GITHUB_TOKEN": "..." }
    }
  }
}
```

## Playbooks

| Prompt | PMBOK 8 processes | Autonomy |
|---|---|---|
| `initiate_project` | Initiate Project or Phase, Identify Stakeholders | L1 draft |
| `develop_scope_structure` | Define Scope, Develop Scope Structure | L1 draft |
| `develop_schedule` | Develop Schedule, Monitor and Control Schedule | L1 draft |
| `identify_and_analyze_risks` | Identify Risks, Perform Risk Analysis, Plan Risk Responses | L3 proposes |
| `monitor_and_control_performance` | Monitor and Control Project Performance, Monitor Risks, Monitor and Control Scope | L1 draft |
| `manage_communications` | Manage Communications, Manage Project Knowledge, Manage Stakeholder Engagement | L3 proposes |

`python -m pm_agent playbook <id> <project>` prints a playbook's full instructions.

## The human side (CLI only)

```bash
python -m pm_agent list demo risk --status proposed
python -m pm_agent approve demo risk R-2 --note "confirmed with design lead"
python -m pm_agent reject demo decision D-1 --reason "revisit after the beta"
python -m pm_agent approve demo charter charter
python -m pm_agent thresholds demo spi_green=0.9 blocked_red=4 --reason "team agreement"
python -m pm_agent audit demo            # who did what, including refused writes
python -m pm_agent history demo risk R-2 # every version with rationale and sources
```

## How health is computed

| Dimension | Green | Amber | Red |
|---|---|---|---|
| Schedule | Monte Carlo P85 on or before the target | P50 on or before the target, P85 after | P50 after the target |
| Earned value | SPI ≥ 0.95 | 0.85 ≤ SPI < 0.95 | SPI < 0.85 |
| Aging WIP | 0 items older than the cycle-time P85 | ≥ 1 | ≥ 3 |
| Blocked | 0 | ≥ 1 | ≥ 3 |
| Risk | Highest *open* risk score < 10 | ≥ 10 | ≥ 15 |

Overall is the worst known dimension. Dimensions without data show as `unknown` and are
listed in `unknown_dimensions`. You can change the thresholds per project (human-only).

## Layout

```
pm_agent/
  schemas/       artifact models (charter, RAID, change requests, status reports, work items...)
  store/         SQLite: versioned artifacts + append-only audit log
  governance/    human-only rules, autonomy levels
  engines/       flow metrics, Monte Carlo forecast, EVM / agile EVM, risk, RAG
  integrations/  GitHub issues sync (read-only)
  playbooks/     PMBOK 8 playbooks (YAML), rendered as MCP prompts
  service.py     operations shared by the CLI and the MCP server
  mcp_server.py  MCP server (agent side)
  cli.py         CLI (human side)
  demo.py        synthetic demo project
  tests/
```

## Known limits and what's next

- Status comes from labels. GitHub Projects' Status field (GraphQL) is next.
- Cycle time starts when the sync first sees an item in progress. Items that go from
  todo to done between syncs only get lead time.
- No writes to GitHub yet. Creating issues (L2) needs the approval queue from Phase 2.
- No scheduled runs yet. A weekly status that runs without you needs our own agent loop
  and a decision on the model provider.
- Google Workspace is used through your MCP host's connectors; this server holds no
  Google credentials.
