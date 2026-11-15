/** The library catalog of the PWA: `/api/library/*` when the Hub announces `hub_library_v1`, the deprecated
 * `/api/nd/*` otherwise. The fallback lives here, not in the screens: it normalizes the ND shapes (design §1) so a
 * page sees one catalog whether the Hub knows the capability or not. */

import { hubLibrarySupported } from '../lib/capabilities';
import { libraryCoverUrl, request, withQuery } from './client';
import type {
  LibAlbumOut,
  LibArtistOut,
  LibArtistPageOut,
  LibHomeOut,
  LibLikedOut,
  LibTrackOut,
  NdAlbumOut,
  NdArtistOut,
  NdArtistPageOut,
  NdHomeOut,
  NdLikedOut,
  NdSongOut,
} from './types';

const COVER_SIZE = 300;

/** The same cover at another size: both branches put the size in the URL. */
export const coverAtSize = (url: string | null | undefined, size: number) =>
  url ? url.replace(/size=\d+/, `size=${size}`) : null;

const atSize = (url: string | null | undefined) => coverAtSize(url, COVER_SIZE) ?? null;

/** A Hub library track as the ND song shape a screen plays: `ref` is `lib:<uid>`, `uid` the primary id. */
export function libSong(t: LibTrackOut): NdSongOut {
  return {
    ref: `lib:${t.uid}`,
    source: 'hm',
    id: t.uid,
    uid: t.uid,
    title: t.title,
    artist: t.artist,
    artists: [t.artist],
    album_title: t.album,
    album_artist: t.album_artist,
    duration_s: t.duration_s,
    track_no: t.track_no,
    disc_no: t.disc_no,
    isrc: null,
    explicit: t.explicit,
    cover_url: t.album_uid ? libraryCoverUrl(t.album_uid, COVER_SIZE) : null,
    cover_small_url: t.album_uid ? libraryCoverUrl(t.album_uid, 120) : null,
    owned: true,
    downloading: false,
    library_track_id: t.id,
    nd_song_id: t.nd_song_id,
    // On the library branch these two hold Hub uids, not Navidrome ids: they are read only to build
    // `/lib/album/<uid>` and `/lib/artist/<uid>` links (`TrackMenu`), never as favorite targets.
    nd_album_id: t.album_uid ?? null,
    nd_artist_id: t.artist_uid ?? null,
  };
}

async function asAlbum(a: LibAlbumOut): Promise<NdAlbumOut> {
  return {
    id: a.uid,
    uid: a.uid,
    lib_uid: a.uid,
    deezer_album_id: a.deezer_album_id,
    title: a.title,
    artist: a.album_artist,
    artist_id: a.artist_uid ?? null,
    year: a.year,
    song_count: a.track_count,
    duration_s: 0,
    cover_url: a.has_cover ? libraryCoverUrl(a.uid, COVER_SIZE) : null,
    starred: false,
    tracks: a.tracks ? a.tracks.map(libSong) : null,
  };
}

function asArtist(a: LibArtistOut): NdArtistOut {
  return {
    id: a.uid,
    uid: a.uid,
    lib_uid: a.uid,
    name: a.name,
    album_count: a.album_count,
    cover_url: a.has_cover ? libraryCoverUrl(a.uid, COVER_SIZE) : null,
    starred: false,
  };
}

/** The ND shapes carry the uid in `id` too (the Hub's own transition does the same), so a page links by `id`. */
function ndAlbum(a: NdAlbumOut): NdAlbumOut {
  return { ...a, uid: a.uid ?? a.id, cover_url: atSize(a.cover_url), tracks: a.tracks ?? null };
}

function ndArtist(a: NdArtistOut): NdArtistOut {
  return { ...a, uid: a.uid ?? a.id, cover_url: atSize(a.cover_url) };
}

// --- the endpoints -----------------------------------------------------------------------------

/** The sorts both branches know (`/api/nd` calls them `type`). */
export type LibAlbumSort =
  | 'recent'
  | 'frequent'
  | 'newest'
  | 'starred'
  | 'alphabeticalByName'
  | 'alphabeticalByArtist'
  | 'random';

export async function libHome(signal?: AbortSignal): Promise<NdHomeOut> {
  if (await hubLibrarySupported()) {
    const h = await request<LibHomeOut>('GET', '/api/library/home', { signal });
    return {
      recent: await Promise.all(h.recent.map(asAlbum)),
      frequent: await Promise.all(h.frequent.map(asAlbum)),
      newest: await Promise.all(h.newest.map(asAlbum)),
      starred: await Promise.all(h.starred.map(asAlbum)),
      radio: h.artists.map(asArtist),
    };
  }
  const h = await request<NdHomeOut>('GET', '/api/nd/home', { signal });
  return {
    recent: h.recent.map(ndAlbum),
    frequent: h.frequent.map(ndAlbum),
    newest: h.newest.map(ndAlbum),
    starred: h.starred.map(ndAlbum),
    radio: h.radio.map(ndArtist),
  };
}

export async function libAlbums(
  sort: LibAlbumSort,
  offset = 0,
  size = 50,
  q?: string,
  signal?: AbortSignal,
): Promise<NdAlbumOut[]> {
  if (await hubLibrarySupported()) {
    const albums = await request<LibAlbumOut[]>(
      'GET',
      withQuery('/api/library/albums', { sort, offset, limit: size, q }),
      { signal },
    );
    return Promise.all(albums.map(asAlbum));
  }
  // the deprecated branch: `type` is the ND name of the same list
  const albums = await request<NdAlbumOut[]>('GET', withQuery('/api/nd/albums', { type: sort, offset, size }), {
    signal,
  });
  return albums.map(ndAlbum);
}

export async function libAlbum(uid: string, signal?: AbortSignal): Promise<NdAlbumOut> {
  if (await hubLibrarySupported()) {
    return asAlbum(await request<LibAlbumOut>('GET', `/api/library/albums/${encodeURIComponent(uid)}`, { signal }));
  }
  return ndAlbum(await request<NdAlbumOut>('GET', `/api/nd/albums/${encodeURIComponent(uid)}`, { signal }));
}

export async function libArtists(signal?: AbortSignal): Promise<NdArtistOut[]> {
  if (await hubLibrarySupported()) {
    const artists = await request<LibArtistOut[]>('GET', '/api/library/artists', { signal });
    return artists.map(asArtist);
  }
  const artists = await request<NdArtistOut[]>('GET', '/api/nd/artists', { signal });
  return artists.map(ndArtist);
}

export async function libArtist(uid: string, signal?: AbortSignal): Promise<NdArtistPageOut> {
  if (await hubLibrarySupported()) {
    const page = await request<LibArtistPageOut>(
      'GET',
      `/api/library/artists/${encodeURIComponent(uid)}`,
      { signal },
    );
    return { artist: asArtist(page.artist), albums: await Promise.all(page.albums.map(asAlbum)) };
  }
  const page = await request<NdArtistPageOut>('GET', `/api/nd/artists/${encodeURIComponent(uid)}`, { signal });
  return { artist: ndArtist(page.artist), albums: page.albums.map(ndAlbum) };
}

/** «Понравившиеся»: the Hub's own ♥ journal (`/api/library/liked`), normalized. */
export async function libLiked(signal?: AbortSignal): Promise<NdLikedOut> {
  if (await hubLibrarySupported()) {
    const liked = await request<LibLikedOut>('GET', '/api/library/liked', { signal });
    return {
      songs: liked.tracks.map(libSong),
      albums: await Promise.all(liked.albums.map(asAlbum)),
      artists: liked.artists.map(asArtist),
    };
  }
  const liked = await request<NdLikedOut>('GET', '/api/nd/liked', { signal });
  return { songs: liked.songs, albums: liked.albums.map(ndAlbum), artists: liked.artists.map(ndArtist) };
}
