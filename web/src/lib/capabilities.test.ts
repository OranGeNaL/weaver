import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { hubLibrarySupported, resetCapabilities, typedFavoritesSupported } from './capabilities';

/** `/healthz` decides the catalog branch (task 4.4): the capability picks `/api/library/*`; no capability, a timeout
 * or an unreachable Hub all keep the deprecated `/api/nd/*` branch, so the PWA never guesses. */

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

beforeEach(() => {
  resetCapabilities();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetCapabilities();
});

describe('hubLibrarySupported', () => {
  it('is on when /healthz lists hub_library_v1', async () => {
    const fetchMock = vi.fn(async () => json({ capabilities: ['typed_favorites_v1', 'hub_library_v1'] }));
    vi.stubGlobal('fetch', fetchMock);
    expect(await hubLibrarySupported()).toBe(true);
    expect(await typedFavoritesSupported()).toBe(true);
    // one /healthz answers the whole session
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('is off when the Hub does not know it', async () => {
    vi.stubGlobal('fetch', async () => json({ capabilities: ['typed_favorites_v1'] }));
    expect(await hubLibrarySupported()).toBe(false);
    expect(await typedFavoritesSupported()).toBe(true);
  });

  it('is off when /healthz times out or is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      async () => {
        throw new DOMException('The operation was aborted', 'AbortError');
      },
    );
    expect(await hubLibrarySupported()).toBe(false);
    expect(await typedFavoritesSupported()).toBe(false);
  });

  it('caches the negative answer, so a timed-out Hub is not asked on every catalog call', async () => {
    const fetchMock = vi.fn(async () => {
      throw new DOMException('The operation was aborted', 'AbortError');
    });
    vi.stubGlobal('fetch', fetchMock);
    expect(await hubLibrarySupported()).toBe(false);
    expect(await hubLibrarySupported()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('is off on a non-200 /healthz', async () => {
    vi.stubGlobal('fetch', async () => new Response('nope', { status: 503 }));
    expect(await hubLibrarySupported()).toBe(false);
  });
});
