# Using vmware-aiops with Cursor

[Cursor](https://www.cursor.com) is an AI-powered code editor with native MCP support. This guide shows how to add `vmware-aiops` as an MCP server so Cursor's AI can manage your VMware infrastructure.

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

## Adding to Cursor

### Option A: Auto-install (recommended)

```bash
vmware-aiops mcp-config install --agent cursor
```

This writes the MCP server config directly into `~/.cursor/mcp.json`.

### Option B: Manual — Cursor Settings UI

1. Open Cursor → **Settings** → **MCP**
2. Click **Add MCP Server**
3. Fill in:
   - **Name**: `vmware-aiops`
   - **Type**: `stdio`
   - **Command**: `vmware-aiops mcp` (v1.5.15+; legacy: `uvx --from vmware-aiops vmware-aiops-mcp`)
   - **Env**: `VMWARE_AIOPS_CONFIG=~/.vmware-aiops/config.yaml`

### Option C: Manual — mcp.json

Add to `~/.cursor/mcp.json` (create if it doesn't exist):

```json
{
  "mcpServers": {
    "vmware-aiops": {
      "command": "vmware-aiops",
      "args": ["mcp"],
      "env": {
        "VMWARE_AIOPS_CONFIG": "~/.vmware-aiops/config.yaml"
      }
    }
  }
}
```

Or use the template generator:

```bash
vmware-aiops mcp-config generate --agent cursor
```

## Available MCP Tools (31 tools)

| Category | Tools |
|----------|-------|
| Inventory | `list_virtual_machines`, `list_esxi_hosts`, `list_all_datastores`, `list_all_clusters` |
| Health | `get_alarms`, `get_events`, `get_vm_info` |
| VM Lifecycle | `power_on_vm`, `power_off_vm`, `set_vm_ttl`, `cancel_vm_ttl`, `list_vm_ttls`, `restore_vm_clean_slate` |
| Deployment | `deploy_vm_from_ova`, `deploy_vm_from_template`, `deploy_linked_clone`, `batch_clone_vms` |
| Guest Operations | `run_vm_guest_command`, `run_vm_guest_command_with_output`, `upload_vm_guest_file`, `download_vm_guest_file` |
| Plan → Apply | `create_vm_plan`, `apply_vm_plan`, `rollback_vm_plan`, `list_vm_plans` |
| Datastore | `browse_datastore`, `scan_all_datastore_images` |

## Usage Examples

**Example 1: Query infrastructure from Cursor chat**
```
You: How many VMs are powered on in my vCenter?

Cursor: [calls list_virtual_machines with power_state=poweredOn]
Found 42 powered-on VMs across 3 clusters.
```

**Example 2: Run a command inside a VM**
```
You: Check disk usage on vm-linux01

Cursor: [calls run_vm_guest_command_with_output]
Filesystem      Size  Used Avail Use%
/dev/sda1        50G   32G   18G  64%
/dev/sdb1       200G  180G   20G  90% ← nearing capacity
```

**Example 3: Deploy a VM from template**
```
You: Clone dev-template to create a new VM named test-env-01

Cursor: [calls create_vm_plan, then apply_vm_plan]
Plan: clone dev-template → test-env-01
Executing... ✓ VM deployed successfully.
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| MCP server not listed | Reload Cursor window after editing mcp.json |
| Auth failure | Run `vmware-aiops doctor` to verify vCenter connectivity |
| `cwd` path error | Use absolute path, not `~` shorthand in mcp.json |
| Tools not appearing | Check Cursor MCP panel for server status and error logs |
