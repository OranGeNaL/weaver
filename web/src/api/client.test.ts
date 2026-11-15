import { describe, expect, it } from 'vitest';

import { isMetered, listenStreamUrl } from './client';

/** `q=mobile` (design §7, task 3.3): a metered or 2G/3G network asks the Hub for the light variant; a fast or unknown
 * network sends no `q` at all. */
describe('listenStreamUrl', () => {
  it('sends no q on a fast or unknown network', () => {
    expect(listenStreamUrl('lib:t-uid', undefined)).toBe('/api/listen/stream?ref=lib%3At-uid');
    expect(listenStreamUrl('lib:t-uid', {})).toBe('/api/listen/stream?ref=lib%3At-uid');
    expect(listenStreamUrl('dz:100', { effectiveType: '4g' })).toBe('/api/listen/stream?ref=dz%3A100');
  });

  it('asks for the mobile variant on saveData and 2G/3G', () => {
    expect(listenStreamUrl('lib:t-uid', { saveData: true })).toBe('/api/listen/stream?ref=lib%3At-uid&q=mobile');
    expect(listenStreamUrl('dz:100', { effectiveType: '3g' })).toBe('/api/listen/stream?ref=dz%3A100&q=mobile');
    expect(listenStreamUrl('dz:100', { effectiveType: '2g' })).toBe('/api/listen/stream?ref=dz%3A100&q=mobile');
  });

  it('reads the metered flag alone', () => {
    expect(isMetered(null)).toBe(false);
    expect(isMetered({ saveData: false, effectiveType: '4g' })).toBe(false);
    expect(isMetered({ saveData: true })).toBe(true);
  });
});
