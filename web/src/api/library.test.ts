import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type * as Capabilities from '../lib/capabilities';

/** Both branches of the catalog (design §1): with `hub_library_v1` the screens see `/api/library/*`, without it the
 * deprecated `/api/nd/*` answers and the adapter normalizes its shape, so a page needs no branch of its own. */

const calls: string[] = [];
let supported = true;

vi.mock('../lib/capabilities', async (importOriginal) => {
  const actual = await importOriginal<typeof Capabilities>();
  return { ...actual, hubLibrarySupported: () => Promise.resolve(supported) };
});

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

function libraryTrack(overrides: Record<string, unknown> = {}) {
  return {
    uid: 't-uid',
    album_uid: 'a-uid',
    artist_uid: 'ar-uid',
    id: 7,
    nd_song_id: 'nd7',
    album_id: 1,
    artist: 'Кино',
    title: 'Группа крови',
    album: 'Группа крови',
    album_artist: 'Кино',
    track_no: 1,
    disc_no: 1,
    year: 1988,
    duration_s: 245,
    bitrate_kbps: 320,
    codec: 'mp3',
    explicit: false,
    source: 'hitmoz',
    locked: false,
    warnings: [],
    path: 'Кино/1988 - Группа крови/1-01 - Группа крови.mp3',
    ...overrides,
  };
}

function libraryAlbum(overrides: Record<string, unknown> = {}) {
  return {
    uid: 'a-uid',
    artist_uid: 'ar-uid',
    id: 1,
    title: 'Группа крови',
    album_artist: 'Кино',
    year: 1988,
    release_type: 'album',
    genre: null,
    deezer_album_id: '500',
    track_count: 11,
    has_cover: true,
    nd_album_id: null,
    tracks: [libraryTrack()],
    ...overrides,
  };
}

const ndAlbum = {
  id: 'nd-a',
  title: 'Группа крови',
  artist: 'Кино',
  artist_id: 'nd-ar',
  year: 1988,
  song_count: 11,
  duration_s: 2000,
  cover_url: '/api/nd/cover/nd-a?size=1000',
  starred: true,
};

beforeEach(() => {
  calls.length = 0;
  supported = true;
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url === '/healthz') throw new Error('the capability mock answers, not /healthz');
    throw new Error(`unexpected request: ${url}`);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

async function load() {
  return import('./library');
}

describe('the library branch (hub_library_v1)', () => {
  it('builds the catalog from /api/library/* and plays `lib:<uid>`', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/library/home')) {
        return json({ recent: [libraryAlbum()], frequent: [], newest: [], starred: [], artists: [{ uid: 'ar-uid', name: 'Кино', album_count: 4, has_cover: true }] });
      }
      if (url.startsWith('/api/library/albums/a-uid')) return json(libraryAlbum());
      if (url.startsWith('/api/library/albums')) return json([libraryAlbum()]);
      if (url.startsWith('/api/library/artists')) return json([{ uid: 'ar-uid', name: 'Кино', album_count: 4, has_cover: true }]);
      if (url.startsWith('/api/library/liked')) return json({ tracks: [libraryTrack()], albums: [libraryAlbum()], artists: [] });
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { libHome, libAlbum, libAlbums, libLiked, libArtists } = await load();

    const home = await libHome();
    expect(home.recent[0].id).toBe('a-uid');
    expect(home.recent[0].cover_url).toBe('/api/library/albums/a-uid/cover?size=300');
    expect(home.radio[0].uid).toBe('ar-uid');

    const album = await libAlbum('a-uid');
    const song = album.tracks![0];
    expect(song.uid).toBe('t-uid');
    expect(song.ref).toBe('lib:t-uid');
    expect(song.owned).toBe(true);
    expect(song.nd_album_id).toBe('a-uid');

    expect((await libAlbums('alphabeticalByName')).length).toBe(1);
    expect((await libArtists())[0].id).toBe('ar-uid');
    const liked = await libLiked();
    expect(liked.songs[0].ref).toBe('lib:t-uid');
    expect(fetchMock.mock.calls.every(([url]) => String(url).startsWith('/api/library/'))).toBe(true);
  });
});

describe('the deprecated fallback (no hub_library_v1)', () => {
  beforeEach(() => {
    supported = false;
  });

  it('asks /api/nd/* and normalizes the ND shapes', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/nd/home')) {
        return json({ recent: [ndAlbum], frequent: [], newest: [], starred: [], radio: [{ id: 'nd-ar', name: 'Кино', album_count: 4, cover_url: null, starred: true }] });
      }
      if (url.startsWith('/api/nd/albums/nd-a')) {
        return json({ ...ndAlbum, tracks: [{ ref: 'dz:100', source: 'dz', id: '100', title: 'Группа крови', artist: 'Кино', duration_s: 245, owned: true, downloading: false, nd_song_id: 'nd7' }] });
      }
      if (url.startsWith('/api/nd/albums?')) return json([ndAlbum]);
      if (url.startsWith('/api/nd/artists')) return json([{ id: 'nd-ar', name: 'Кино', album_count: 4, cover_url: null, starred: true }]);
      if (url.startsWith('/api/nd/liked')) return json({ songs: [], albums: [ndAlbum], artists: [] });
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { libHome, libAlbum, libAlbums, libArtists, libLiked } = await load();

    const home = await libHome();
    // the ND id is carried into `uid`, so a page links the same way in both branches
    expect(home.recent[0].id).toBe('nd-a');
    expect(home.recent[0].uid).toBe('nd-a');
    expect(home.recent[0].cover_url).toBe('/api/nd/cover/nd-a?size=300');
    expect(home.recent[0].song_count).toBe(11);
    expect(home.radio[0].uid).toBe('nd-ar');

    const album = await libAlbum('nd-a');
    expect(album.tracks![0].ref).toBe('dz:100');
    expect(album.tracks![0].uid).toBeUndefined();

    expect((await libAlbums('alphabeticalByName'))[0].id).toBe('nd-a');
    expect((await libArtists())[0].uid).toBe('nd-ar');
    expect((await libLiked()).albums[0].id).toBe('nd-a');
    expect(fetchMock.mock.calls.every(([url]) => String(url).startsWith('/api/nd/'))).toBe(true);
  });
});

describe('cover helpers', () => {
  it('replaces the size of either branch', async () => {
    const { coverAtSize } = await load();
    const { libraryCoverUrl: fromClient } = await import('./client');
    expect(fromClient('a-uid', 600)).toBe('/api/library/albums/a-uid/cover?size=600');
    expect(fromClient('a-uid', 600, 7)).toBe('/api/library/albums/a-uid/cover?size=600&v=7');
    expect(coverAtSize('/api/library/albums/a-uid/cover?size=300', 600)).toBe('/api/library/albums/a-uid/cover?size=600');
    expect(coverAtSize('/api/nd/cover/nd-a?size=1000', 300)).toBe('/api/nd/cover/nd-a?size=300');
    expect(coverAtSize(null, 300)).toBeNull();
  });
});
