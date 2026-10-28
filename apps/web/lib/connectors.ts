import type { Connector } from './types';

/**
 * Whether an app can actually be connected today.
 *
 * `GET /api/connectors` reports this as `available`. The catalogue is much
 * larger than the set of working integrations, so this is the field that
 * decides whether a tile offers a Connect button or admits there is nothing
 * behind it yet. A service build that predates the field falls back to
 * `has_adapter`, which carried the same meaning.
 */
export function isAvailable(connector: Connector): boolean {
  return connector.available ?? connector.has_adapter;
}

export function connectedCount(connectors: readonly Connector[]): number {
  return connectors.reduce((total, connector) => total + (connector.connected ? 1 : 0), 0);
}
