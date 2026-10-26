# Using vmware-aiops with LocalCowork

[LocalCowork](https://github.com/liquid-ai/LocalCowork) is an AI collaboration platform by Liquid AI that runs locally. This guide shows how to add `vmware-aiops` as an MCP server so LocalCowork can manage your VMware infrastructure.

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

## Adding to LocalCowork

Edit the LocalCowork MCP config JSON (typically `~/.localcowork/mcp_config.json` or set via the UI):

```json
{
  "vmware-aiops": {
    "command": "python",
    "args": ["-m", "vmware_aiops.mcp_server"],
    "cwd": "/path/to/VMware-AIops",
    "env": {
      "VMWARE_AIOPS_CONFIG": "~/.vmware-aiops/config.yaml"
    }
  }
}
```

Replace `/path/to/VMware-AIops` with your actual clone path.

A ready-to-use template is also available at `examples/mcp-configs/localcowork.json`.

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

**Example 1: Inventory overview**
```
You: List all VMs that are powered off

LocalCowork: [calls list_virtual_machines with power_state=off]
Found 4 powered-off VMs:
- vm-backup01 (4 vCPU, 8GB RAM)
- vm-test02 (2 vCPU, 4GB RAM)
- vm-old-db (8 vCPU, 32GB RAM)
- vm-staging (4 vCPU, 16GB RAM)
```

**Example 2: Safe multi-step deployment**
```
You: Deploy a new VM from template "ubuntu-22.04-base" with 4 vCPU and 8GB RAM

LocalCowork: [calls create_vm_plan]
Plan ready:
  - Source: ubuntu-22.04-base
  - Resources: 4 vCPU / 8GB RAM
  - Target datastore: datastore01 (free: 1.2TB)

Confirm to apply? (yes/no)
You: yes
[calls apply_vm_plan] ✓ VM deployed in 45s
```

**Example 3: Guest execution**
```
You: Check nginx status on vm-web01

LocalCowork: [calls run_vm_guest_command]
● nginx.service - A high performance web server
   Loaded: loaded (/lib/systemd/system/nginx.service)
   Active: active (running) since Mon 2026-03-10 08:12:33 UTC; 23h ago
```
