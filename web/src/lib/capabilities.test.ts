import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resetCapabilities, typedFavoritesSupported } from './capabilities';

/** `/healthz` answers the capability questions of a session; a Hub without `typed_favorites_v1`, a timeout or an
 * unreachable Hub all mean the older ♥ protocol, so the PWA never guesses. */

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

beforeEach(() => {
  resetCapabilities();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetCapabilities();
});

describe('typedFavoritesSupported', () => {
  it('is on when /healthz lists typed_favorites_v1, one answer per session', async () => {
    const fetchMock = vi.fn(async () => json({ capabilities: ['typed_favorites_v1', 'listening_checkpoints_v1'] }));
    vi.stubGlobal('fetch', fetchMock);
    expect(await typedFavoritesSupported()).toBe(true);
    expect(await typedFavoritesSupported()).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('is off when the Hub does not know it', async () => {
    vi.stubGlobal('fetch', async () => json({ capabilities: ['listening_checkpoints_v1'] }));
    expect(await typedFavoritesSupported()).toBe(false);
  });

  it('is off when /healthz times out or is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      async () => {
        throw new DOMException('The operation was aborted', 'AbortError');
      },
    );
    expect(await typedFavoritesSupported()).toBe(false);
  });

  it('caches the negative answer, so a timed-out Hub is not asked on every call', async () => {
    const fetchMock = vi.fn(async () => {
      throw new DOMException('The operation was aborted', 'AbortError');
    });
    vi.stubGlobal('fetch', fetchMock);
    expect(await typedFavoritesSupported()).toBe(false);
    expect(await typedFavoritesSupported()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('is off on a non-200 /healthz', async () => {
    vi.stubGlobal('fetch', async () => new Response('nope', { status: 503 }));
    expect(await typedFavoritesSupported()).toBe(false);
  });
});
