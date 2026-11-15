import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type * as Wave from './waveSeed';

/** «Радиостанции» and «Радио по исполнителю»: the wave is seeded with the Hub artist `uid`, and a library track with
 * its Hub track `uid` (`lib:<uid>` is its ref) — there is no other kind of artist id any more. */

beforeEach(() => {
  // `lib/embedded.ts` reads window/navigator at import time
  vi.stubGlobal('window', { location: { href: 'https://hub.test/#/', hash: '#/' }, addEventListener: () => undefined });
  vi.stubGlobal('navigator', { userAgent: 'vitest', locks: undefined });
  vi.stubGlobal('localStorage', {
    length: 0,
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
    key: () => null,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

async function load(): Promise<typeof Wave> {
  return import('./waveSeed');
}

describe('artistRadioSeed', () => {
  it('sends the Hub artist uid', async () => {
    const { artistRadioSeed } = await load();
    expect(artistRadioSeed({ uid: 'a-uid' })).toEqual({ artist_uid: 'a-uid' });
  });

  it('keys the in-flight spinner by that uid', async () => {
    const { artistRadioSeed, seedKey } = await load();
    expect(seedKey(artistRadioSeed({ uid: 'a-uid' }))).toBe('a-uid');
    expect(seedKey({ artist_ref: 'dz:500' })).toBe('dz:500');
    expect(seedKey({ song_uid: 't-uid' })).toBe('t-uid');
    expect(seedKey({ ref: 'lib:t-uid', by_artist: true })).toBe('artist:lib:t-uid');
  });
});
