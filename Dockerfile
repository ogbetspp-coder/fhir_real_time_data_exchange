# Pinned by digest, not tag: the normalisation version (docs/fidelity-normalization.md section
# 8, ADR 0003) depends on the runtime's Unicode database, and a rebuilt tag can move it while
# every version literal stays put. This is node:22.14.0-bookworm-slim's multi-arch index as
# resolved on 2026-09-20; test/runtime.test.ts fails the build if the Unicode version drifts.
FROM node:26.8.2-bookworm-slim@sha256:cd9f682fa2885cd1056e830424764158570061c59736a1da836bc3d73df095ae AS build

WORKDIR /app
COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY tsconfig.json tsconfig.build.json ./
COPY src ./src
RUN npm run build
RUN npm prune --omit=dev

FROM node:26.8.2-bookworm-slim@sha256:cd9f682fa2885cd1056e830424764158570061c59736a1da836bc3d73df095ae AS runtime

ENV NODE_ENV=production
WORKDIR /app
USER node
COPY --chown=node:node --from=build /app/node_modules ./node_modules
COPY --chown=node:node --from=build /app/dist ./dist
COPY --chown=node:node package.json ./
COPY --chown=node:node fhir/mappings ./fhir/mappings

EXPOSE 8080
CMD ["node", "dist/server.js"]
