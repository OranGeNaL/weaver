import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type * as Wave from './waveSeed';

/** «Радиостанции» and «Радио по исполнителю» (task 3.3 / council D1): the library branch must seed the wave with the
 * Hub `artist_uid` (the Hub rejects a lone `nd_artist_id`), the deprecated `/api/nd` branch with the ND id. */

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
  it('sends the Hub artist uid when /api/library answered', async () => {
    const { artistRadioSeed } = await load();
    expect(artistRadioSeed({ id: 'a-uid', lib_uid: 'a-uid' })).toEqual({ artist_uid: 'a-uid' });
  });

  it('keeps the deprecated Navidrome artist id on the /api/nd branch', async () => {
    const { artistRadioSeed } = await load();
    expect(artistRadioSeed({ id: 'nd-ar' })).toEqual({ nd_artist_id: 'nd-ar' });
  });

  it('keys the in-flight spinner by whichever id is sent', async () => {
    const { artistRadioSeed, seedKey } = await load();
    expect(seedKey(artistRadioSeed({ id: 'a-uid', lib_uid: 'a-uid' }))).toBe('a-uid');
    expect(seedKey(artistRadioSeed({ id: 'nd-ar' }))).toBe('nd-ar');
  });
});
