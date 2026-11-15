import { api } from '../api/client';
import type { LaunchOrigin, TrackOut, WaveOut } from '../api/types';
import { newLaunch, routeContext } from './plays';

/** Playback through the HomeMusic Android app: its WebView exposes `HomeMusicAndroid.playQueue(itemsJson, index)`,
 * which queues library songs (`lib:<uid>`) and catalog tracks in the app's own player. A plain browser has no
 * player here, so its pages play themselves (`lib/listen.ts`). Asked at render time, not at import, so a bridge
 * injected later still counts; only one of the two players plays at a time. */
type Bridge = {
  playQueue?: (itemsJson: string, index: number) => void;
  playWave?: (waveJson: string) => void;
  pauseNative?: () => void;
  webPlayer?: (visible: boolean) => void;
};

/** The app's player started (sent by the app): page listening stops. */
export const NATIVE_PLAYING_EVENT = 'hm:native-playing';

function bridge(): Bridge | undefined {
  // SAFETY: the Android WebView injects `HomeMusicAndroid` into `window`; no DOM type declares it, and a page
  // without the bridge answers `undefined` (checked by every `canPlay*` guard).
  return (window as unknown as { HomeMusicAndroid?: Bridge }).HomeMusicAndroid;
}

/** The app's player plays catalog tracks and library songs in one queue (app 1.2.0+): the page plays nothing itself. */
export function canPlayAll(): boolean {
  return typeof bridge()?.playQueue === 'function';
}

/** The app's player plays «Моя волна» and asks the Hub for its next tracks itself (app 1.6.0+, `playWave`). */
export function canPlayWave(): boolean {
  return canPlayAll() && typeof bridge()?.playWave === 'function';
}

/** Hands a started wave (the answer of `POST /api/wave/start`) to the app's player. Needs `canPlayWave()`. */
export function playWave(wave: WaveOut) {
  bridge()?.playWave?.(JSON.stringify(wave));
}

/** A track the Hub streams by `ref` (`/api/listen/stream`): a catalog track, or a provider's file ("file:…"). */
export interface HubItem {
  ref: string;
  title: string;
  artist: string;
  album?: string | null;
  cover_url?: string | null;
  duration_s?: number | null;
}

/** An entry of `playQueue`: a library song as `lib:<uid>`, or a track the Hub streams by its ref. `context` (app
 * 1.5.0+) — where the list was played from, for the app's listening events (MIXES.md 3.2); `launch_id` /
 * `launch_origin` — this launch of the list, the same on every entry (INSIGHTS.md 6.5). */
type QueueItem = {
  ref: string;
  title: string;
  artist: string;
  album: string | null;
  cover_url: string | null;
  duration_s: number | null;
} & { context?: string; launch_id?: string; launch_origin?: LaunchOrigin | null };

const absolute = (url: string | null | undefined) => (url ? new URL(url, window.location.href).href : null);

const hubItem = (t: HubItem): QueueItem => ({
  ref: t.ref,
  title: t.title,
  artist: t.artist,
  album: t.album ?? null,
  cover_url: absolute(t.cover_url),
  duration_s: t.duration_s ?? null,
});

/** Plays `list` (an album, search results…) in the app's player from `from` on: a library track is handed over as
 * `{ref: "lib:<uid>"}` (the app streams it through the Hub), the rest as the file a download would take. Needs
 * `canPlayAll()`. `context` — where it was played from (`mix:<id>`…); by default the screen (`routeContext`).
 * `origin` — where it was launched (by default by `context`). */
export function playTracks(list: readonly TrackOut[], from?: TrackOut, context?: string, origin?: LaunchOrigin) {
  const items: QueueItem[] = list.map((t) => {
    const asTrack = { ...t, artist: t.artists?.length ? t.artists.join(', ') : t.artist, album: t.album_title };
    return hubItem(t.uid ? { ...asTrack, ref: `lib:${t.uid}` } : asTrack);
  });
  playQueue(items, from ? Math.max(0, list.indexOf(from)) : 0, context, origin);
}

/** Plays provider files the Hub streams (the Files tab of the search) in the app's player. Needs `canPlayAll()`. */
export function playHubItems(list: readonly HubItem[], index: number, context?: string) {
  playQueue(list.map(hubItem), index, context);
}

function playQueue(items: QueueItem[], index: number, context = routeContext(), origin?: LaunchOrigin) {
  const b = bridge();
  if (!b?.playQueue || items.length === 0) return;
  window.dispatchEvent(new Event(NATIVE_PLAYING_EVENT));
  const launch = newLaunch(context, origin);
  b.playQueue(JSON.stringify(items.map((it) => ({ ...it, context, launch_id: launch.id, launch_origin: launch.origin }))), index);
  // The Hub starts picking the files of the first catalog tracks now (it keeps a pick for 30 min and the player's
  // request waits for the same pick), so the first one and the one after it start sooner.
  for (const it of items.slice(index)) {
    // a library song streams from the Hub too: its pick is what the player waits for
    api.listen(it.ref).catch(() => {
      /* the player shows the error when it gets there */
    });
    if (it !== items[index]) break;
  }
}

/** Pauses the app's player because the page starts playing (no-op in a browser and in app versions before 1.1.1). */
export function pauseNative() {
  const b = bridge();
  if (typeof b?.pauseNative === 'function') b.pauseNative();
}

/** The page's mini player appeared or went away: the app hides its own player meanwhile (1.1.2+). */
export function webPlayerShown(visible: boolean) {
  const b = bridge();
  if (typeof b?.webPlayer === 'function') b.webPlayer(visible);
}
