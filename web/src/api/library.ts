/** The library catalog of the PWA: `/api/library/*` with the Hub's uids (`lib:<uid>` plays a track). */

import { libraryCoverUrl, request, withQuery } from './client';
import type {
  LibAlbumOut,
  LibArtistOut,
  LibArtistPageOut,
  LibHomeOut,
  LibLikedOut,
  LibSongOut,
  LibTrackOut,
} from './types';

const COVER_SIZE = 300;

/** The same cover at another size: the size is in the URL. */
export const coverAtSize = (url: string | null | undefined, size: number) =>
  url ? url.replace(/size=\d+/, `size=${size}`) : null;

/** The cover of a library album or artist by its `uid`; null — it has none. */
export const libCover = (entity: { uid: string; has_cover: boolean }, size = COVER_SIZE): string | null =>
  entity.has_cover ? libraryCoverUrl(entity.uid, size) : null;

/** A Hub library track as the rows play it: `ref` is `lib:<uid>`, `uid` the primary id. */
export function libSong(t: LibTrackOut): LibSongOut {
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
    album_uid: t.album_uid ?? null,
    artist_uid: t.artist_uid ?? null,
  };
}

/** «Понравившиеся»: the Hub's own ♥ journal, its tracks as rows play them. */
export interface LibLikedView {
  songs: LibSongOut[];
  albums: LibAlbumOut[];
  artists: LibArtistOut[];
}

// --- the endpoints -----------------------------------------------------------------------------

export type LibAlbumSort =
  | 'recent'
  | 'frequent'
  | 'newest'
  | 'starred'
  | 'alphabeticalByName'
  | 'alphabeticalByArtist'
  | 'random';

export const libHome = (signal?: AbortSignal) => request<LibHomeOut>('GET', '/api/library/home', { signal });

export const libAlbums = (sort: LibAlbumSort, offset = 0, size = 50, q?: string, signal?: AbortSignal) =>
  request<LibAlbumOut[]>('GET', withQuery('/api/library/albums', { sort, offset, limit: size, q }), { signal });

export const libAlbum = (uid: string, signal?: AbortSignal) =>
  request<LibAlbumOut>('GET', `/api/library/albums/${encodeURIComponent(uid)}`, { signal });

export const libArtists = (signal?: AbortSignal) =>
  request<LibArtistOut[]>('GET', '/api/library/artists', { signal });

export const libArtist = (uid: string, signal?: AbortSignal) =>
  request<LibArtistPageOut>('GET', `/api/library/artists/${encodeURIComponent(uid)}`, { signal });

export async function libLiked(signal?: AbortSignal): Promise<LibLikedView> {
  const liked = await request<LibLikedOut>('GET', '/api/library/liked', { signal });
  return { songs: liked.tracks.map(libSong), albums: liked.albums, artists: liked.artists };
}
