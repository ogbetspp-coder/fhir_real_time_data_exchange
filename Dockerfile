FROM node:22.14.0-bookworm-slim AS build

WORKDIR /app
COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY tsconfig.json tsconfig.build.json ./
COPY src ./src
RUN npm run build
RUN npm prune --omit=dev

FROM node:22.14.0-bookworm-slim AS runtime

ENV NODE_ENV=production
WORKDIR /app
USER node
COPY --chown=node:node --from=build /app/node_modules ./node_modules
COPY --chown=node:node --from=build /app/dist ./dist
COPY --chown=node:node package.json ./
COPY --chown=node:node fhir/mappings ./fhir/mappings

EXPOSE 8080
CMD ["node", "dist/server.js"]
