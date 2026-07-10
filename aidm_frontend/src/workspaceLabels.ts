import type { AccountWorkspace } from './types'

export function savedWorkspaceRoleLabel(workspace: AccountWorkspace) {
  if (workspace.is_workspace_admin && workspace.workspace_role !== 'admin') {
    return `${workspace.workspace_role} / admin`
  }
  return workspace.workspace_role
}

export function savedWorkspaceDisplayName(workspace: AccountWorkspace) {
  return workspace.table_name || workspace.workspace_name || workspace.workspace_id
}
