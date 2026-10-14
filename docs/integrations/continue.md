# Using vmware-aiops with Continue

[Continue](https://github.com/continuedev/continue) is an open-source AI code assistant for VS Code and JetBrains that supports local and cloud models via MCP. This guide shows how to add `vmware-aiops` as an MCP server.

## Prerequisites

1. **Install vmware-aiops**
   ```bash
   uv tool install vmware-aiops
   ```

2. **Configure credentials**
   ```bash
   mkdir -p ~/.vmware-aiops
   cat > ~/.vmware-aiops/config.yaml << 'EOF'
   targets:
     my-vcenter:
       host: vcenter.example.com
       username: administrator@vsphere.local
       password_env: VMWARE_PASSWORD
       verify_ssl: false
   EOF

   echo "VMWARE_PASSWORD=your_password" > ~/.vmware-aiops/.env
   chmod 600 ~/.vmware-aiops/.env
   ```

3. **Verify setup**
   ```bash
   vmware-aiops doctor
   ```

## Adding to Continue

Add to your Continue config file (`~/.continue/config.yaml`):

```yaml
mcpServers:
  - name: vmware-aiops
    command: python
    args:
      - -m
      - vmware_aiops.mcp_server
    cwd: /path/to/VMware-AIops
    env:
      VMWARE_AIOPS_CONFIG: ~/.vmware-aiops/config.yaml
```

Replace `/path/to/VMware-AIops` with your actual clone path.

A ready-to-use template is available at `examples/mcp-configs/continue.yaml`.

### With Ollama (Local Model)

```yaml
# ~/.continue/config.yaml
models:
  - title: Qwen2.5 32B (local)
    provider: ollama
    model: qwen2.5:32b

mcpServers:
  - name: vmware-aiops
    command: python
    args: [-m, vmware_aiops.mcp_server]
    cwd: /path/to/VMware-AIops
    env:
      VMWARE_AIOPS_CONFIG: ~/.vmware-aiops/config.yaml
```

> **Tip for local models**: Use CLI mode instead of MCP to reduce token overhead. Small models (< 32B) perform better with CLI commands (~2K tokens) vs MCP tool schemas (~10K tokens).

## Available MCP Tools (31 tools)

| Category | Tools |
|----------|-------|
| Inventory | `list_virtual_machines`, `list_esxi_hosts`, `list_all_datastores`, `list_all_clusters` |
| Health | `get_alarms`, `get_events`, `get_vm_info` |
| VM Lifecycle | `power_on_vm`, `power_off_vm`, `set_vm_ttl`, `cancel_vm_ttl`, `list_vm_ttls`, `restore_vm_clean_slate` |
| Deployment | `deploy_vm_from_ova`, `deploy_vm_from_template`, `deploy_linked_clone`, `attach_iso_to_vm`, `convert_vm_to_template`, `batch_clone_vms`, `batch_linked_clone_vms`, `batch_deploy_from_spec` |
| Guest Operations | `run_vm_guest_command`, `upload_vm_guest_file`, `download_vm_guest_file` |
| Plan → Apply | `create_vm_plan`, `apply_vm_plan`, `rollback_vm_plan`, `list_vm_plans` |
| Datastore | `browse_datastore`, `scan_all_datastore_images`, `list_cached_images` |

All tools accept an optional `target` parameter to switch between environments.

## Usage Examples

**Example 1: Quick health check while coding**
```
You: @vmware-aiops Are there any critical alarms right now?

Continue: [calls get_alarms]
2 critical alarms on prod-vcenter:
- vm-db01: Memory usage at 98% (threshold: 90%)
- esxi-host02: CPU ready time high (12ms avg)
```

**Example 2: VM info during debugging**
```
You: @vmware-aiops Get details for vm-app01

Continue: [calls get_vm_info]
vm-app01:
  Power: ON | vCPU: 8 | RAM: 32GB (28GB used)
  Guest OS: Ubuntu 22.04 | IP: 10.0.1.45
  Snapshots: 2 (oldest: 14 days ago)
  Datastore: ssd-ds01 (free: 450GB)
```

**Example 3: Deploy test VM with auto-cleanup**
```
You: @vmware-aiops Deploy a test VM from ubuntu-22-base with 4h TTL

Continue: [calls create_vm_plan → apply_vm_plan → set_vm_ttl]
✓ vm-test-1741694400 deployed
✓ TTL set: auto-delete at 2026-03-12 08:00 UTC
```
