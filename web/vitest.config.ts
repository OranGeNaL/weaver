import { defineConfig } from 'vitest/config';

/** Unit tests of the pieces that are pure logic on the client: the catalog fallback (`api/library.ts`), the stream
 * URL and the payload of the Android bridge. Screens are covered by `typecheck`/`build` and the manual run. */
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
});
