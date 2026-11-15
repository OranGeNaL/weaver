import type { HealthOut, PlayEventIn } from '../api/types';

let supported = false;
let checkpointsSupported = false;
let librarySupported = false;
let checkedAt = 0;
let checking: Promise<boolean | null> | null = null;

/** One `/healthz` answers all the capability questions of a session; a later request checks again. */
async function check(signal?: AbortSignal): Promise<void> {
  if (checking) return checking.then(() => undefined);
  checking = (async () => {
    let result: boolean | null = null;
    try {
      const r = await fetch('/healthz', { cache: 'no-store', signal: signal ?? AbortSignal.timeout(5000) });
      if (!r.ok) throw new Error('health');
      const caps = ((await r.json()) as HealthOut).capabilities ?? [];
      supported = caps.includes('typed_favorites_v1');
      checkpointsSupported = caps.includes('listening_checkpoints_v1');
      librarySupported = caps.includes('hub_library_v1');
      checkedAt = Date.now();
      result = supported;
    } catch {
      supported = false;
      checkpointsSupported = false;
      librarySupported = false;
      checkedAt = Date.now(); // a timed-out Hub is not asked again on every catalog call
    } finally {
      checking = null;
    }
    return result;
  })();
  await checking;
}

/** Unknown/offline servers are treated conservatively; a later request checks again. */
export async function typedFavoritesSupported(): Promise<boolean | null> {
  if (Date.now() - checkedAt < 30_000) return supported;
  await check();
  return supported;
}

/** The Hub serves the catalog, the covers and library listening through `/api/library/*` and `lib:<uid>`:
 * without it (an older server, or `/healthz` unreachable) the PWA keeps the deprecated `/api/nd/*` branch. */
export async function hubLibrarySupported(): Promise<boolean> {
  if (Date.now() - checkedAt < 30_000) return librarySupported;
  await check();
  return librarySupported;
}

/** Tests and a re-login: forget what the last `/healthz` said. */
export function resetCapabilities() {
  checkedAt = 0;
  supported = false;
  checkpointsSupported = false;
  librarySupported = false;
}

export function canSendPlayEvent(event: PlayEventIn): boolean {
  if (['progress', 'interrupted', 'error'].includes(event.kind) && !checkpointsSupported) return false;
  return supported || ((!event.target_type || event.target_type === 'track') && !event.reverts_event_id);
}
