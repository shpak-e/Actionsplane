/** Human-friendly labels for the raw bulk-edit operation names (e.g. "set-permissions"). */
export const OPERATION_LABEL: Record<string, string> = {
  "pin-shas": "Pin actions to SHAs",
  "set-permissions": "Set least-privilege permissions",
  "swap-runs-on": "Update runner image",
  "bump-action-version": "Upgrade action version",
  "add-concurrency": "Add a concurrency group",
  "inject-step": "Add a step",
};

export function operationLabel(op: string): string {
  return OPERATION_LABEL[op] ?? op.replace(/-/g, " ");
}
