import { describe, expect, it } from 'vitest';
import snapshot from '../openapi-snapshot.json';

describe('openapi snapshot', () => {
  it('locks Phase 8a capability paths and components', () => {
    const paths = recordAt(snapshot, 'paths');
    const schemas = recordAt(recordAt(snapshot, 'components'), 'schemas');

    expect(paths['/api/brokers/{id}/capabilities']).toBeDefined();
    expect(paths['/api/admin/order-capabilities']).toBeDefined();
    expect(paths['/api/admin/brokers/{label}/account-hashes']).toBeDefined();

    const capabilityPath = recordAt(paths, '/api/brokers/{id}/capabilities');
    const get = recordAt(capabilityPath, 'get');
    const responses = recordAt(get, 'responses');
    const ok = recordAt(responses, '200');
    const content = recordAt(recordAt(ok, 'content'), 'application/json');
    expect(content.schema).toEqual({ $ref: '#/components/schemas/BrokerCapabilitiesResponse' });

    expect(recordAt(paths, '/api/admin/order-capabilities').post).toBeDefined();
    expect(recordAt(paths, '/api/admin/brokers/{label}/account-hashes').get).toBeDefined();

    for (const component of [
      'BrokerCapabilitiesResponse',
      'OrderTypeRow',
      'TimeInForceRow',
      'CapabilityComboRow',
      'OrderCapabilityWrite',
    ]) {
      expect(schemas[component]).toBeDefined();
    }
  });
});

function recordAt(source: unknown, key: string): Record<string, unknown> {
  expect(source).toBeTypeOf('object');
  expect(source).not.toBeNull();
  const value = (source as Record<string, unknown>)[key];
  expect(value).toBeTypeOf('object');
  expect(value).not.toBeNull();
  return value as Record<string, unknown>;
}
