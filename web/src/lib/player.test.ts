import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { TrackOut } from '../api/types';
import { playSongs, playTracks } from './player';

/** The WebView bridge payload (task 3.2): a library track goes to the app as `{ref: "lib:<uid>"}`, so the app plays
 * it through the Hub; `{song_id: nd_song_id}` stays as the deprecated form until stage 7. */

type Payload = Record<string, unknown>;

let queued: Payload[] = [];
let songs: string[] = [];

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
  songs = [];
  vi.stubGlobal('window', {
    location: { href: 'https://hub.test/#/library', hash: '#/library' },
    dispatchEvent: () => true,
    addEventListener: () => undefined,
    HomeMusicAndroid: {
      playQueue: (items: string) => {
        queued = JSON.parse(items) as Payload[];
      },
      play: (ids: string) => {
        songs = JSON.parse(ids) as string[];
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
    playTracks([track({ uid: 't-uid', owned: true, nd_song_id: 'nd7', album_title: 'Группа крови' })]);
    expect(queued).toHaveLength(1);
    expect(queued[0].ref).toBe('lib:t-uid');
    expect(queued[0]).not.toHaveProperty('song_id');
    expect(queued[0].title).toBe('Группа крови');
  });

  it('keeps the deprecated `{song_id}` for a library track without a uid', () => {
    playTracks([track({ owned: true, nd_song_id: 'nd7' })]);
    expect(queued[0]).toEqual(expect.objectContaining({ song_id: 'nd7' }));
    expect(queued[0]).not.toHaveProperty('ref');
  });

  it('streams a catalog track by its ref', () => {
    playTracks([track({ ref: 'dz:100' })]);
    expect(queued[0].ref).toBe('dz:100');
  });
});

describe('playSongs (the old bridge)', () => {
  it('still names library songs by their deprecated Navidrome id', () => {
    playSongs(['nd7', 'nd8'], 1);
    expect(songs).toEqual(['nd7', 'nd8']);
  });
});
