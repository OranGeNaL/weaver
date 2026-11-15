import type { WaveSeedIn } from '../api/types';

/** The key of a wave seed: identifies the in-flight start and the player's wave. */
export const seedKey = (seed?: WaveSeedIn | null) =>
  (seed?.by_artist ? 'artist:' : '') +
  (seed?.ref ?? seed?.song_uid ?? seed?.artist_uid ?? seed?.artist_ref ?? '');

/** The seed of a library artist's radio: the Hub artist `uid`. */
export const artistRadioSeed = (artist: { uid: string }): WaveSeedIn => ({ artist_uid: artist.uid });
