import { describe, it, expect, vi } from "vitest";
import { connectStart, getTokenStatus, disconnect, enableTier2 } from "./schwab";

describe("services/schwab.ts", () => {
  it("connectStart redirects to oauth-start", () => {
    const win = { location: { href: "" } } as unknown as Window;
    connectStart(win);
    expect(win.location.href).toContain("/api/admin/brokers/schwab/oauth-start");
  });

  it("getTokenStatus parses ISO timestamps", async () => {
    const fetchMock = vi.fn(async (url: RequestInfo | URL) => {
      expect(String(url)).toContain("/api/admin/brokers/schwab/status");
      return {
        ok: true,
        status: 200,
        json: async () => ({
          refresh_token_issued_at: "2026-04-30T12:00:00+00:00",
          access_token_issued_at: "2026-04-30T12:00:00+00:00",
          tier2_refresh_enabled: false,
          tier2_consecutive_failures: 0,
        }),
      } as Response;
    });
    const status = await getTokenStatus(fetchMock as unknown as typeof fetch);
    expect(status.refreshTokenIssuedAt).toEqual(new Date("2026-04-30T12:00:00Z"));
    expect(status.tier2RefreshEnabled).toBe(false);
    expect(status.tier2ConsecutiveFailures).toBe(0);
  });

  it("disconnect passes delete_credentials flag in querystring", async () => {
    const fetchMock: typeof fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({}),
    }) as Response) as unknown as typeof fetch;
    await disconnect(fetchMock, { deleteCredentials: true });
    const calls = (fetchMock as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    expect(calls.length).toBeGreaterThan(0);
    const lastCall = String(calls[calls.length - 1]?.[0] ?? "");
    expect(lastCall).toContain("delete_credentials=true");
  });

  it("enableTier2 PUTs to admin config endpoint", async () => {
    const fetchMock: typeof fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({}),
    }) as Response) as unknown as typeof fetch;
    await enableTier2(fetchMock, true);
    const calls = (fetchMock as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    expect(calls.length).toBeGreaterThan(0);
    const firstUrl = String(calls[0]?.[0] ?? "");
    expect(firstUrl).toContain("/api/admin/config/broker/schwab.tier2_refresh_enabled");
  });
});
