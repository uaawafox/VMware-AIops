# Operating vmware-aiops with a local / small model

Claude-class models drive this skill without special instruction. Smaller and
locally-hosted models — Llama 3.3 70B, Qwen, Mistral, and similar, served
through Goose, Ollama, or OpenShift AI — need explicit operating rules to call
tools reliably.

This page exists because an operator wrote those rules by hand first. The
guardrails below are adapted, with thanks, from the working configuration
[@juanpf-ha](https://github.com/juanpf-ha) developed while running
vmware-monitor and vmware-aria against a production vSphere estate with Llama
3.3 70B FP8 on an on-prem H100
([VMware-AIops#31](https://github.com/zw008/VMware-AIops/issues/31)). The
cross-skill rules are identical across this family; the parts below marked
vmware-aiops are specific to this skill.

vmware-aiops carries the family's largest write surface — 35 of its 49 MCP
tools change state, including `vm_delete`, cluster deletion and guest command
execution. Of every skill here, this is the one where a model's discipline
should not be the only thing standing between a prompt and a destroyed VM.

> **Disclaimer**: This is a community-maintained open-source project and is
> **not affiliated with, endorsed by, or sponsored by VMware, Inc. or Broadcom
> Inc.** "VMware" and "vSphere" are trademarks of Broadcom.

---

## First: the rules you no longer need to write

Several guardrails from the original configuration are now enforced by the
skill itself. Prompt instructions are advisory — a model can ignore them.
These are structural, so it cannot.

| Guardrail you would otherwise prompt for | Now enforced by |
|---|---|
| "Work exclusively in read-only mode and never modify anything" | **Read-only mode.** Set `VMWARE_READ_ONLY=true` and every write-effecting tool is removed from the registry at startup. `list_tools()` never offers them, so the model cannot call what it cannot see. |
| "Never power off, delete or reconfigure a VM without asking" | Same gate. In read-only mode there is no `vm_power_off`, no `vm_delete`, no `vm_reconfigure` to call — 36 tools go, leaving 13. |
| "Do not run commands inside guest operating systems" | The guest-ops tools are all write-classified, `vm_guest_download` included: it reads the guest but writes to an operator-supplied local path with guest credentials, so the gate withholds it too. |
| "Use explicit limits for queries that may return large amounts of data" | **The list envelope.** `browse_datastore`, `list_vcenter_alarms`, `vm_list_plans`, `vm_list_snapshots` and `vm_list_ttl` return `{items, returned, limit, total, truncated, hint}`, so the model reads truncation instead of guessing at it. |
| "If a listing came back empty, say so rather than claiming the call failed" | Same envelope. Empty `items` with `truncated: false` means checked-and-none — a stated result, not a silence the model has to interpret. |
| "Log every state change you make" | **The `@vmware_tool` decorator.** Every write is recorded to `~/.vmware/audit.db` before the model sees the result, and policy rules are evaluated ahead of execution. Neither depends on the model cooperating. |
| "Ask a human before doing something irreversible in production" | **Policy.** A target declared `environment: production` requires a named approver (`VMWARE_AUDIT_APPROVED_BY`) for irreversible work. |

### Turning read-only mode on

One variable covers every skill in the family:

```json
{
  "mcpServers": {
    "vmware-aiops": {
      "command": "vmware-aiops",
      "args": ["mcp"],
      "env": { "VMWARE_READ_ONLY": "true" }
    }
  }
}
```

Per-skill override — useful when this skill alone should stay writable:

```bash
VMWARE_READ_ONLY=true          # whole family read-only
VMWARE_AIOPS_READ_ONLY=false   # …except VM lifecycle
```

Or permanently, in `~/.vmware-aiops/config.yaml`:

```yaml
read_only: true
```

Precedence is per-skill env → family env → config file → off. The startup log
lists exactly which tools were withheld, and `vmware-aiops doctor` reports the
resolved state and its source. An unparseable value (`VMWARE_READ_ONLY=ture`)
enables read-only mode rather than silently ignoring the typo.

A blocked tool is a lockdown, not a fault. When a write tool is missing from
`list_tools()`, the model should name the operation it cannot perform and say
an operator must clear the switch — not retry, and not go looking for a
different tool that achieves the same change.

---

## The system prompt

Everything below still benefits from being stated explicitly. Copy this into
your agent's instruction block.

```text
## Tool use

- Always call an MCP tool before answering any question about the current
  VMware environment. Never answer from memory or assumption.
- Never describe a tool call, and never output a JSON example, instead of
  executing the tool. If you intend to call a tool, call it.
- If a tool fails, report the actual error text. Do not complete the answer
  with assumptions about what the result would have been.
- Use explicit limits on queries that may return large amounts of data. Do not
  request unlimited results unless the user asks for them.
- Before any write, restate the exact object you are about to change and wait
  for the user to confirm it. VM names are case-sensitive and near-duplicates
  are common.

## Skill routing

- vmware-aiops: VM lifecycle (power, create, clone, migrate, delete,
  snapshots), OVA/template deployment, guest operations, clusters, plan/apply.
- vmware-monitor: read-only vCenter inventory, hosts, datastores, alarms,
  events, performance. Prefer it for any question that only reads.
- vmware-storage: iSCSI, vSAN, datastore capacity.
- vmware-vks: Supervisor, namespaces, Tanzu Kubernetes clusters.
- vmware-nsx / vmware-nsx-security: networking and firewall.
- vmware-aria: Aria Operations metrics, alerts, capacity.
- vmware-pilot: multi-step workflows that need approval gates.

## Data fidelity

- Never invent infrastructure objects, metrics, alarms, events, or
  relationships. If a tool did not return it, it does not exist for this answer.
- Preserve the exact power state, task state, status and criticality values the
  tools return. Do not translate, normalise, or prettify enum values.
- If a requested field was not returned, show it as "not available". Do not
  infer it from other fields.
- Preserve the original order and the full set of fields when the user asks
  for specific ones.
- When a response is long, report every item it contains. If a result is
  truncated, the tool says so explicitly — report the truncation rather than
  describing the visible subset as the whole.

## Analysis discipline

- Separate observed data from interpretation. State which is which.
- Do not claim a capacity, performance, or configuration problem unless the
  tool output contains explicit supporting evidence.
- Avoid generic recommendations that are not directly supported by the results.

## Writes in vmware-aiops

- A write tool missing from the tool list means read-only mode is on. Name the
  blocked operation and stop. Do not retry and do not substitute another tool.
- reset_vcenter_alarm has a blast radius: vSphere has no per-alarm clear API,
  so it clears every triggered alarm matching the named alarm's entity type and
  status, not only the one named. Report the response's scope field verbatim.
- vm_set_ttl schedules an unattended auto-delete. Treat it as destructive and
  say so when proposing it.
- Long writes return a task id instead of blocking. Poll vm_task_status. A
  "still running" message is not a failure — never re-issue the operation.
- Use vm_guest_exec_output rather than vm_guest_exec when the user wants the
  command's output; the latter returns only an exit code.
```

---

## Known failure modes on small models

Observed with Llama 3.3 70B FP8 (Goose, on-prem H100), and useful as a
checklist when evaluating any local model against these skills:

| Symptom | Mitigation |
|---|---|
| Describes a tool call, or emits a JSON example, instead of executing it | The "never describe a tool call" rule above. Also check your harness is not echoing tool schemas into context — models imitate the nearest format they see. |
| Long tool responses: omits items, or reports "no data returned" when data was present | Ask for explicit limits so responses stay small. Check the envelope's `truncated` / `returned` / `total` fields rather than trusting the model's summary — a "no data" claim is checkable against `returned`. |
| Adds generic recommendations unsupported by results | The "analysis discipline" rules. |
| Drops requested fields or reorders results | State the required fields and ordering in the request itself, not only in the system prompt. |
| Multi-tool workflows take 30–50s end to end | Prefer the aggregate tools — `cluster_health_summary`, `vm_investigation_bundle`, `host_investigation_bundle`, `datastore_investigation_bundle`, `cross_vcenter_attention` — which collapse a 3-4 call sequence into one round trip. |
| Picks a write tool for a question that only reads | Run read-only, or route read questions to vmware-monitor. A model that can see 35 write tools will sometimes reach for one to "check" something. |
| Treats a long-running task's "still running" reply as a failure and re-issues the write | The `vm_task_status` rule above. A re-issued clone or delete is the worst outcome in this skill. |
| Assumes an alarm reset cleared only the alarm it named | Report `scope` from the response. The clear is entity-type-wide by design. |

## Reporting results

Local-model compatibility is an explicit design constraint for this family, and
the evidence base is small. If you evaluate a model against this skill —
Qwen, Mistral, Granite, or anything else — a report of what worked and what did
not is genuinely useful:
[github.com/zw008/VMware-AIops/issues](https://github.com/zw008/VMware-AIops/issues).
