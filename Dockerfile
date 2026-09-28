# The worker's and the query service's image, one file with a target for each (audit B07, S-5):
#
#   docker build --target worker .     # CMD node dist/server.js
#   docker build --target query .      # CMD node dist/query/server.js
#
# Until B07 the query image had a file of its own that differed from this one only in its CMD.
#
# The Node binary is pinned by digest, not tag: the normalisation version
# (docs/fidelity-normalization.md section 8, ADR 0003) depends on the runtime's Unicode database,
# and a rebuilt tag can move it while every version literal stays put. This is
# node:22.22.0-bookworm-slim's multi-arch index as resolved on 2026-09-22 (ICU 77.1, Unicode
# 16.0). Its binary carries its own ICU, so the operating system under it can move without moving
# either; the runtime stage below asserts both, and test/runtime.test.ts checks CI's own Node.
FROM node:22.22.0-bookworm-slim@sha256:dd9d21971ec4395903fa6143c2b9267d048ae01ca6d3ea96f16cb30df6187d94 AS build

WORKDIR /app
COPY package.json package-lock.json ./
# No install scripts: the build needs tsc and nothing a package's install script produces, and a
# script would run in the build with the network (audit B07, S-5). engine-strict refuses a Node
# other than package.json's engines.
RUN npm ci --no-audit --no-fund --ignore-scripts --engine-strict
COPY tsconfig.json tsconfig.build.json ./
COPY src ./src
RUN npm run build
RUN npm prune --omit=dev --ignore-scripts

# The operating system under the Node binary (audit B07, S-1). node:22.22.0-bookworm-slim stopped
# being rebuilt when 22.22.1 was published (2026-03-04), so its Debian froze on the day it was
# built and collected fixed advisories it could never receive; the Node pin itself kept it from
# being refreshed. The runtime now starts from debian:bookworm-slim by digest, which Dependabot
# moves weekly (docker-library rebuilds it on each Debian update), and takes only the pinned Node
# binary from the image above. The binary needs glibc, libstdc++ and libgcc, all in the base.
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS runtime

COPY --from=build /usr/local/bin/node /usr/local/bin/node
# The runtime ADR 0003 pins, asserted in the image that ships rather than in CI's own Node: a
# different binary, or one whose ICU or Unicode moved, fails the build here.
RUN ["node", "-e", "const v = process.versions; const want = { node: '22.22.0', icu: '77.1', unicode: '16.0' }; for (const [key, value] of Object.entries(want)) { if (v[key] !== value) { console.error(key + ' is ' + v[key] + ', not ' + value); process.exit(1); } } if (new Intl.NumberFormat('de').format(1234.5) !== '1.234,5') { console.error('not a full-ICU build'); process.exit(1); } console.log('node ' + v.node + ', ICU ' + v.icu + ', Unicode ' + v.unicode);"]
RUN groupadd --gid 1000 node \
    && useradd --uid 1000 --gid node --shell /bin/bash --create-home node

ENV NODE_ENV=production
WORKDIR /app
USER node
COPY --chown=node:node --from=build /app/node_modules ./node_modules
COPY --chown=node:node --from=build /app/dist ./dist
COPY --chown=node:node package.json ./
COPY --chown=node:node fhir/mappings ./fhir/mappings
# The pinned standards the run manifest names (src/fhir/standards-lock.ts, run manifest 4.0.0).
COPY --chown=node:node fhir/standards.lock.json ./fhir/standards.lock.json
EXPOSE 8080

FROM runtime AS query
CMD ["node", "dist/query/server.js"]

# Last, so a build without --target is the worker, as it always was.
FROM runtime AS worker
CMD ["node", "dist/server.js"]
