import { useEffect, useState } from 'react';
import type { FavoriteType } from '../api/types';
import { markLikePending, toggleLike, useLiked } from '../lib/stars';
import { IconButton, cx } from './ui';

/** Typed favorite toggle. A catalog track may download after ♥; album/artist favorites never download it.
 * `pending` is a legacy hint; an explicit own unlike always overrides it. */
export function LikeButton({
  uid,
  refId,
  pending,
  title,
  size = 20,
  className,
  targetType = 'track',
  data,
}: {
  /** the Hub uid of the target (a track, album or artist) */
  uid?: string | null;
  refId?: string | null;
  pending?: boolean;
  title: string;
  size?: number;
  className?: string;
  targetType?: FavoriteType;
  data?: Record<string, unknown>;
}) {
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (targetType === 'track' && pending && refId && !uid) markLikePending(refId);
  }, [pending, refId, uid, targetType]);
  const target = { targetType, uid, ref: refId, data };
  const liked = useLiked(target);
  if (!refId && !uid) return null;
  return (
    <IconButton
      icon="heart"
      filled={liked}
      size={size}
      aria-pressed={liked}
      label={liked ? `Убрать «${title}» из понравившихся` : `«${title}» — в понравившиеся`}
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try { await toggleLike(target); } finally { setBusy(false); }
      }}
      className={cx(liked ? '!text-accent' : 'text-muted hover:text-fg', className)}
    />
  );
}
