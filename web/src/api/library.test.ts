import { afterEach, describe, expect, it, vi } from 'vitest';

/** The library catalog of the PWA: `/api/library/*` with the Hub's uids — a library song plays as `lib:<uid>`. */

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

function libraryTrack(overrides: Record<string, unknown> = {}) {
  return {
    uid: 't-uid',
    album_uid: 'a-uid',
    artist_uid: 'ar-uid',
    id: 7,
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
    tracks: [libraryTrack()],
    ...overrides,
  };
}

const libraryArtist = { uid: 'ar-uid', name: 'Кино', album_count: 4, has_cover: true };

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

async function load() {
  return import('./library');
}

describe('the library catalog', () => {
  it('asks /api/library/* and plays library songs as `lib:<uid>`', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/library/home')) {
        return json({ recent: [libraryAlbum()], frequent: [], newest: [], starred: [], artists: [libraryArtist] });
      }
      if (url.startsWith('/api/library/albums/a-uid')) return json(libraryAlbum());
      if (url.startsWith('/api/library/albums')) return json([libraryAlbum()]);
      if (url.startsWith('/api/library/artists/ar-uid')) return json({ artist: libraryArtist, albums: [libraryAlbum()] });
      if (url.startsWith('/api/library/artists')) return json([libraryArtist]);
      if (url.startsWith('/api/library/liked')) return json({ tracks: [libraryTrack()], albums: [libraryAlbum()], artists: [libraryArtist] });
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { libHome, libAlbum, libAlbums, libArtist, libArtists, libLiked } = await load();

    const home = await libHome();
    expect(home.recent[0].uid).toBe('a-uid');
    expect(home.artists[0].uid).toBe('ar-uid');

    // an album page: the Hub's track rows, converted by `libSong` where the page plays them
    const album = await libAlbum('a-uid');
    expect(album.tracks?.[0].uid).toBe('t-uid');

    expect((await libAlbums('alphabeticalByName')).length).toBe(1);
    expect((await libArtists())[0].uid).toBe('ar-uid');
    expect((await libArtist('ar-uid')).albums[0].uid).toBe('a-uid');

    const liked = await libLiked();
    const song = liked.songs[0];
    expect(song.ref).toBe('lib:t-uid');
    expect(song.uid).toBe('t-uid');
    expect(song.owned).toBe(true);
    expect(song.album_uid).toBe('a-uid');
    expect(song.artist_uid).toBe('ar-uid');
    expect(song.library_track_id).toBe(7);
    expect(song.cover_url).toBe('/api/library/albums/a-uid/cover?size=300');
    expect(liked.albums[0].uid).toBe('a-uid');
    expect(liked.artists[0].uid).toBe('ar-uid');
    expect(fetchMock.mock.calls.every(([url]) => String(url).startsWith('/api/library/'))).toBe(true);
  });

  it('builds the cover URL of an album or artist by its uid, or null without one', async () => {
    const { libCover } = await load();
    const { libraryCoverUrl } = await import('./client');
    expect(libCover(libraryAlbum())).toBe('/api/library/albums/a-uid/cover?size=300');
    expect(libCover(libraryAlbum(), 600)).toBe('/api/library/albums/a-uid/cover?size=600');
    expect(libCover(libraryArtist, 120)).toBe('/api/library/albums/ar-uid/cover?size=120');
    expect(libCover({ uid: 'x', has_cover: false })).toBeNull();
    expect(libraryCoverUrl('a-uid', 600, 7)).toBe('/api/library/albums/a-uid/cover?size=600&v=7');
  });
});

describe('cover helpers', () => {
  it('replaces the size in a cover URL', async () => {
    const { coverAtSize } = await load();
    expect(coverAtSize('/api/library/albums/a-uid/cover?size=300', 600)).toBe('/api/library/albums/a-uid/cover?size=600');
    expect(coverAtSize(null, 300)).toBeNull();
  });
});
