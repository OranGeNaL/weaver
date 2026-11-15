import type { WaveSeedIn } from '../api/types';

/** The key of a wave seed: identifies the in-flight start and the player's wave. */
export const seedKey = (seed?: WaveSeedIn | null) =>
  (seed?.by_artist ? 'artist:' : '') +
  (seed?.ref ?? seed?.song_uid ?? seed?.nd_song_id ?? seed?.artist_uid ?? seed?.artist_ref ?? seed?.nd_artist_id ?? '');

/** The seed of a library artist's radio: its Hub `uid` when the catalog came from `/api/library/*`, else the
 * deprecated Navidrome artist id (the Hub accepts only `artist_uid` in the library branch). */
export const artistRadioSeed = (artist: { id: string; lib_uid?: string | null }): WaveSeedIn =>
  artist.lib_uid ? { artist_uid: artist.lib_uid } : { nd_artist_id: artist.id };
