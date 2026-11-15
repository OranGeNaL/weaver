import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { TrackOut } from '../api/types';
import { canPlayAll, playTracks } from './player';

/** The WebView bridge payload (task 6.8 of the decommission): a library track goes to the app as
 * `{ref: "lib:<uid>"}`, so the app streams it through the Hub — there is no `{song_id}` form any more. */

type Payload = Record<string, unknown>;

let queued: Payload[] = [];

function track(overrides: Partial<TrackOut> = {}): TrackOut {
  return {
    ref: 'dz:100',
    source: 'dz',
    id: '100',
    title: 'Группа крови',
    artist: 'Кино',
    owned: false,
    downloading: false,
    ...overrides,
  } as TrackOut;
}

beforeEach(() => {
  queued = [];
  vi.stubGlobal('window', {
    location: { href: 'https://hub.test/#/library', hash: '#/library' },
    dispatchEvent: () => true,
    addEventListener: () => undefined,
    HomeMusicAndroid: {
      playQueue: (items: string) => {
        queued = JSON.parse(items) as Payload[];
      },
    },
  });
  vi.stubGlobal('localStorage', {
    length: 0,
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
    key: () => null,
  });
  vi.stubGlobal(
    'fetch',
    async () => new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('playTracks', () => {
  it('hands a library track over as `lib:<uid>`', () => {
    playTracks([track({ uid: 't-uid', owned: true, album_title: 'Группа крови' })]);
    expect(queued).toHaveLength(1);
    expect(queued[0].ref).toBe('lib:t-uid');
    expect(queued[0]).not.toHaveProperty('song_id');
    expect(queued[0].title).toBe('Группа крови');
  });

  it('streams a catalog track by its ref', () => {
    playTracks([track({ ref: 'dz:100' })]);
    expect(queued[0].ref).toBe('dz:100');
    expect(queued[0]).not.toHaveProperty('song_id');
  });

  it('starts at the tapped track', () => {
    playTracks([track({ ref: 'dz:1' }), track({ uid: 'u2', owned: true, ref: 'dz:2' }), track({ ref: 'dz:3' })], track({ uid: 'u2', owned: true, ref: 'dz:2' }));
    expect(queued.map((x) => x.ref)).toEqual(['dz:1', 'lib:u2', 'dz:3']);
  });
});

describe('canPlayAll', () => {
  it('is on with the app bridge and off in a plain browser', () => {
    expect(canPlayAll()).toBe(true);
    vi.stubGlobal('window', { addEventListener: () => undefined });
    expect(canPlayAll()).toBe(false);
  });
});
