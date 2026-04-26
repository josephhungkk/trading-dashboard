export class MaintenanceError extends Error {
  constructor(public window: 'weekend' | 'daily', public until: string) {
    super(`broker_maintenance ${window} until ${until}`);
    this.name = 'MaintenanceError';
  }
}

export class SidecarUnreachableError extends Error {
  constructor(public label: string) {
    super(`sidecar_unreachable label=${label}`);
    this.name = 'SidecarUnreachableError';
  }
}
